"""Encrypted consistent PostgreSQL backup and guarded, empty restore-lab only restore.

Plaintext archives/dumps are confined to the operator container's /work tmpfs.
Only allowlisted recovery materials are included; no recursive .local traversal.
"""

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql

SECRET_FILES = {
    "secrets/environment.env": ".env",
    "secrets/demo-accounts.json": ".local/demo-accounts.json",
    "secrets/workflow-accounts.json": ".local/workflow-accounts.json",
    "secrets/report-reviewer.json": ".local/report-reviewer/workflow-accounts.json",
    "secrets/audit-seal.json": ".local/audit-seal.json",
}
MAX_UNPACKED = 192 * 1024 * 1024  # Dump + tar + verification copy must fit /work (1 GiB).
RESTORE_ID = re.compile(r"^orgproekt-restore-[0-9]{14}-[a-f0-9]{8}$")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_write(path, data):
    path = Path(path)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)


def run(args, **kwargs):
    # Neither subprocess diagnostics nor exception strings are forwarded: they can contain secrets.
    result = subprocess.run(args, stderr=subprocess.PIPE, timeout=600, **kwargs)
    if result.returncode:
        raise RuntimeError("operator_command_failed")
    return result


def env_from(path):
    config = dict(dotenv_values(path, interpolate=False))
    for name in ("DB_ADMIN_PASSWORD", "DB_APP_PASSWORD", "MFA_ENCRYPTION_KEY", "AUDIT_HMAC_KEY"):
        if not config.get(name):
            raise ValueError("missing_recovery_secret")
    return config


def pg_env(config):
    return {
        **os.environ,
        "PGHOST": "db",
        "PGPORT": "5432",
        "PGDATABASE": "orgproekt",
        "PGUSER": "orgproekt_owner",
        "PGPASSWORD": config["DB_ADMIN_PASSWORD"],
        "PGCONNECT_TIMEOUT": "10",
        "PGOPTIONS": "-c timezone=UTC -c DateStyle=ISO,YMD",
    }


def connect(config):
    return psycopg.connect(
        host="db",
        dbname="orgproekt",
        user="orgproekt_owner",
        password=config["DB_ADMIN_PASSWORD"],
        connect_timeout=10,
        options="-c timezone=UTC -c DateStyle=ISO,YMD",
    )


def normalize_constraint(definition):
    # PostgreSQL parse/deparse distributes varchar[] -> text[] over literal array
    # elements during pg_restore. Normalize ONLY this equivalent literal form;
    # retain every other cast, operator, function, literal and parenthesis.
    literal = r"'(?:[^']|'')*'::character varying"
    definition = re.sub(r"\((" + literal + r")\)::text", r"\1", definition)
    return re.sub(
        r"\(ARRAY\[((?:" + literal + r")(?:, " + literal + r")*)\]\)::text\[\]",
        r"ARRAY[\1]",
        definition,
    )


def fingerprints(conn):
    """Row multiset hashes include all columns, bytea files, auth state and embeddings."""
    tables = {}
    for (name,) in conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall():
        query = sql.SQL(
            "SELECT encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex') "
            "FROM {} t ORDER BY 1"
        ).format(sql.Identifier("public", name))
        digest, count = hashlib.sha256(), 0
        for (row_hash,) in conn.execute(query):
            digest.update(row_hash.encode())
            count += 1
        tables[name] = {"rows": count, "sha256": digest.hexdigest()}
    security_queries = [
        "SELECT c.relname,c.relkind,r.rolname,c.relacl::text,c.relrowsecurity,c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='public' ORDER BY 1,2",
        "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d "
        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,a.attnum",
        "SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2",
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k "
        "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' ORDER BY 1,2",
        "SELECT p.proname,pg_get_function_identity_arguments(p.oid),p.prosrc,p.proacl::text,"
        "p.prosecdef,p.proconfig::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' ORDER BY 1,2",
        "SELECT schemaname,sequencename,start_value,min_value,max_value,increment_by,cycle,last_value "
        "FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
        "SELECT nspname,nspacl::text FROM pg_namespace WHERE nspname='public'",
        "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname",
    ]
    schema = [conn.execute(query).fetchall() for query in security_queries]
    schema[3] = [
        (table, name, normalize_constraint(definition)) for table, name, definition in schema[3]
    ]
    return {"tables": tables, "schema_sha256": hashlib.sha256(canonical(schema)).hexdigest()}


def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o600
    archive.addfile(info, io.BytesIO(data))


def unpack_checked(tar_path, destination):
    """Authenticate age first; then enforce exact file allowlist, sizes and content hashes."""
    with tarfile.open(tar_path, "r:") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        allowed = {"manifest.json", "database.dump", *SECRET_FILES}
        if (
            len(names) != len(set(names))
            or not set(names) <= allowed
            or not {"manifest.json", "database.dump", "secrets/environment.env"} <= set(names)
            or any(not m.isfile() or m.size < 0 for m in members)
            or sum(m.size for m in members) > MAX_UNPACKED
            or archive.getmember("manifest.json").size > 4 * 1024 * 1024
        ):
            raise ValueError("invalid_archive_members")
        manifest = json.load(archive.extractfile("manifest.json"))
        if manifest.get("format") != 1 or set(manifest.get("files", {})) != set(names) - {
            "manifest.json"
        }:
            raise ValueError("invalid_manifest")
        for member in members:
            if member.name == "manifest.json":
                continue
            target = destination / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.extractfile(member) as stream, target.open("xb") as output:
                os.chmod(target, 0o600)
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != manifest["files"][member.name]:
                raise ValueError("archive_checksum_mismatch")
        with (destination / "database.dump").open("rb") as dump:
            if dump.read(5) != b"PGDMP":
                raise ValueError("invalid_postgres_dump")
        return manifest


def decrypt(archive, key, temp, expected_sha256):
    if file_hash(archive) != expected_sha256.lower():
        raise ValueError("encrypted_checksum_mismatch")
    temp.mkdir(parents=True, exist_ok=True)
    tar_path = temp / "payload.tar"
    run(
        ["age", "--decrypt", "-i", str(key), "-o", str(tar_path), str(archive)],
        stdout=subprocess.DEVNULL,
    )
    # No file is consumed before age has authenticated the ENTIRE stream.
    return unpack_checked(tar_path, temp / "verified")


def new_key(path):
    # age-keygen refuses existing output; suppress public-key diagnostics too.
    if path.exists():
        raise FileExistsError("key_exists")
    run(["age-keygen", "-o", str(path)], stdout=subprocess.DEVNULL)
    path.chmod(0o600)
    return {"operation": "keygen", "result": "created"}


def create(source, output, key, app_image, web_image):
    started = time.monotonic()
    config = env_from(source / ".env")
    if config.get("APP_MODE", "development") not in {"development", "test"}:
        raise ValueError("unverified_deployment_mode")
    if any(
        (source / folder).exists() and any((source / folder).iterdir()) for folder in ("uploads",)
    ):
        raise ValueError("external_file_storage_not_supported")
    materials = {}
    for name, relative in SECRET_FILES.items():
        path = source / relative
        if path.is_symlink():
            raise ValueError("symlink_recovery_material")
        if path.exists():
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("oversized_recovery_material")
            materials[name] = path.read_bytes()
    recipient = run(["age-keygen", "-y", str(key)], stdout=subprocess.PIPE).stdout.decode().strip()
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    final = output / ("orgproekt-" + backup_id + ".tar.age")
    partial = output / (final.name + ".partial")
    if final.exists() or partial.exists():
        raise FileExistsError("backup_exists")
    try:
        with tempfile.TemporaryDirectory(dir="/work") as work:
            temp = Path(work)
            with connect(config) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.execute("SET LOCAL statement_timeout='120s'")
                version = conn.execute("SHOW server_version_num").fetchone()[0]
                if int(version) // 10000 != 17:
                    raise ValueError("postgres_major_not_supported")
                snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
                before = fingerprints(conn)
                run(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--compress=6",
                        "--snapshot=" + snapshot,
                        "--file=" + str(temp / "database.dump"),
                    ],
                    env=pg_env(config),
                    stdout=subprocess.DEVNULL,
                )
                revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
                # Verify the effective encryption/audit keys, without changing source rows.
                validate_secrets(config)
            for name, content in materials.items():
                if (source / SECRET_FILES[name]).read_bytes() != content:
                    raise ValueError("recovery_material_changed_during_backup")
            manifest = {
                "format": 1,
                "backup_id": backup_id,
                "created_at": datetime.now(UTC).isoformat(),
                "postgres_version": version,
                "migration": revision,
                "app_image": app_image,
                "web_image": web_image,
                "tools": Path("/app/ops/tool-versions.txt").read_text(),
                "fingerprints": before,
                "files": {
                    name: hashlib.sha256(data).hexdigest() for name, data in materials.items()
                },
            }
            manifest["files"]["database.dump"] = file_hash(temp / "database.dump")
            estimated = (temp / "database.dump").stat().st_size + sum(map(len, materials.values()))
            if estimated > MAX_UNPACKED:
                raise ValueError("backup_exceeds_tmpfs_limit")
            # Stream tar directly into age; the host only ever receives ciphertext.
            with partial.open("xb") as encrypted:
                os.chmod(partial, 0o600)
                process = subprocess.Popen(
                    ["age", "-r", recipient],
                    stdin=subprocess.PIPE,
                    stdout=encrypted,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    with tarfile.open(fileobj=process.stdin, mode="w|") as archive:
                        add_bytes(archive, "manifest.json", canonical(manifest))
                        archive.add(
                            temp / "database.dump", arcname="database.dump", recursive=False
                        )
                        for name, data in materials.items():
                            add_bytes(archive, name, data)
                    process.stdin.close()
                    if process.wait(timeout=600):
                        raise RuntimeError("encryption_failed")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                encrypted.flush()
                os.fsync(encrypted.fileno())
            # Verify decryption and every file before publishing a usable filename.
            decrypt(partial, key, temp / "check", file_hash(partial))
            os.link(partial, final)  # atomic no-clobber publication, same filesystem
            partial.unlink()
        receipt = {
            "operation": "backup",
            "result": "verified",
            "file": final.name,
            "sha256": file_hash(final),
            "bytes": final.stat().st_size,
            "tables": len(before["tables"]),
            "rows": sum(t["rows"] for t in before["tables"].values()),
            "seconds": round(time.monotonic() - started, 2),
        }
        private_write(output / (final.name + ".receipt.json"), canonical(receipt) + b"\n")
        return receipt
    finally:
        partial.unlink(missing_ok=True)


def configure_app(config):
    from sqlalchemy.engine import URL

    os.environ.update(
        {
            "APP_MODE": "development",
            "APP_ORIGIN": config.get("APP_ORIGIN", "http://localhost:8082"),
            "DATABASE_URL": URL.create(
                "postgresql+psycopg",
                username="orgproekt_app",
                password=config["DB_APP_PASSWORD"],
                host="db",
                database="orgproekt",
            ).render_as_string(hide_password=False),
            "BROKER_URL": "amqp://disabled:disabled@unavailable//",
            "GEMINI_ENABLED": "false",
            "MFA_ENCRYPTION_KEY": config["MFA_ENCRYPTION_KEY"],
            "AUDIT_HMAC_KEY": config["AUDIT_HMAC_KEY"],
        }
    )
    from app.config import settings

    settings.cache_clear()


def validate_secrets(config):
    configure_app(config)
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app import audit
    from app.config import settings

    engine = create_engine(settings().database_url.get_secret_value())
    try:
        with Session(engine) as session:
            result = audit.verify_chain(session)
            if not result["valid"]:
                raise ValueError("audit_key_or_chain_invalid")
        with connect(config) as conn:
            for secret, pending in conn.execute(
                "SELECT mfa_secret,pending_mfa_secret FROM user_accounts"
            ):
                for value in (secret, pending):
                    if value:
                        Fernet(config["MFA_ENCRYPTION_KEY"].encode()).decrypt(value.encode())
        return result
    finally:
        engine.dispose()


def prepare(archive, key, destination, expected, project):
    if not RESTORE_ID.fullmatch(project) or destination.exists():
        raise ValueError("restore_requires_new_lab_directory")
    destination.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(dir="/work") as work:
        temp = Path(work)
        manifest = decrypt(archive, key, temp, expected)
        config = env_from(temp / "verified/secrets/environment.env")
        config.update(
            {
                "DB_ADMIN_PASSWORD": secrets.token_urlsafe(32),
                "DB_APP_PASSWORD": secrets.token_urlsafe(32),
                "RABBITMQ_PASSWORD": secrets.token_urlsafe(32),
                "APP_MODE": "development",
                "APP_ORIGIN": "http://localhost:8082",
                "GEMINI_ENABLED": "false",
                "GEMINI_API_KEY": "",
                "GEMINI_MODEL": "",
                "RESTORE_ID": project,
                "RESTORE_APP_IMAGE": manifest["app_image"],
                "RESTORE_WEB_IMAGE": manifest["web_image"],
            }
        )
        # Write only known configuration keys; never interpolate untrusted dotenv syntax.
        names = [
            "DB_ADMIN_PASSWORD",
            "DB_APP_PASSWORD",
            "RABBITMQ_PASSWORD",
            "MFA_ENCRYPTION_KEY",
            "AUDIT_HMAC_KEY",
            "APP_MODE",
            "APP_ORIGIN",
            "GEMINI_ENABLED",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "RESTORE_ID",
            "RESTORE_APP_IMAGE",
            "RESTORE_WEB_IMAGE",
        ]
        if any(not re.fullmatch(r"[A-Za-z0-9_:/=.@+\-]*", config.get(n, "")) for n in names):
            raise ValueError("unsupported_environment_encoding")
        private_write(
            destination / "restore.env", "".join(f"{n}={config[n]}\n" for n in names).encode()
        )
        for name in manifest["files"]:
            if name.startswith("secrets/") and name != "secrets/environment.env":
                private_write(
                    destination / name.removeprefix("secrets/"),
                    (temp / "verified" / name).read_bytes(),
                )
        guard = {"project": project, "archive_sha256": expected.lower(), "format": 1}
        private_write(destination / "restore.guard.json", canonical(guard))
        private_write(destination / "manifest.json", canonical(manifest))
    return {"operation": "prepare", "result": "ready", "project": project}


def require_empty_lab(conn, project):
    marker = conn.execute("SELECT current_setting('orgproekt.restore_id',true)").fetchone()[0]
    if not RESTORE_ID.fullmatch(project) or marker != project:
        raise ValueError("restore_target_is_not_isolated_lab")
    if conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]:
        raise ValueError("restore_target_is_not_empty")


def restore(archive, key, private, expected):
    started = time.monotonic()
    config = env_from(private / "restore.env")
    guard = json.loads((private / "restore.guard.json").read_text())
    project = config.get("RESTORE_ID", "")
    if guard != {"project": project, "archive_sha256": expected.lower(), "format": 1}:
        raise ValueError("restore_guard_mismatch")
    with connect(config) as conn:
        require_empty_lab(conn, project)
    with tempfile.TemporaryDirectory(dir="/work") as work:
        temp = Path(work)
        manifest = decrypt(archive, key, temp, expected)
        run(
            [
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--dbname=orgproekt",
                str(temp / "verified/database.dump"),
            ],
            env=pg_env(config),
            stdout=subprocess.DEVNULL,
        )
    with connect(config) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        restored = fingerprints(conn)
        if restored != manifest["fingerprints"]:
            raise ValueError("restored_data_or_privileges_mismatch")
        bad_documents = conn.execute(
            "SELECT count(*) FROM document_originals o JOIN document_versions v ON v.id=o.version_id "
            "WHERE encode(sha256(o.content),'hex')<>v.sha256"
        ).fetchone()[0]
        bad_reports = conn.execute(
            "SELECT count(*) FROM report_releases WHERE encode(sha256(pdf),'hex')<>pdf_sha256 "
            "OR encode(sha256(docx),'hex')<>docx_sha256"
        ).fetchone()[0]
        if bad_documents or bad_reports:
            raise ValueError("stored_file_digest_mismatch")
    audit_result = validate_secrets(config)
    # First prove exact restore, THEN isolate copied authentication challenges in the clone only.
    configure_app(config)
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app import audit
    from app.config import settings

    engine = create_engine(settings().database_url.get_secret_value())
    with Session(engine) as db:
        sessions = db.execute(
            text("UPDATE browser_sessions SET revoked=true WHERE NOT revoked")
        ).rowcount
        db.execute(text("UPDATE recovery_tokens SET used_at=now() WHERE used_at IS NULL"))
        if db.scalar(text("SELECT to_regclass('public.passkey_challenges') IS NOT NULL")):
            db.execute(text("DELETE FROM passkey_challenges"))
        db.execute(
            text(
                "UPDATE account_invitations SET cancelled_at=now(),delivery_secret=NULL,"
                "version=version+1 WHERE accepted_at IS NULL AND cancelled_at IS NULL"
            )
        )
        audit.record(
            db,
            "system.restore_isolated",
            None,
            category="security",
            payload={
                "origin": "host_operator",
                "backup_sha256": expected,
                "revoked_sessions": sessions,
            },
        )
        db.commit()
    engine.dispose()
    report = {
        "operation": "restore",
        "result": "verified",
        "project": project,
        "tables": len(restored["tables"]),
        "rows": sum(t["rows"] for t in restored["tables"].values()),
        "schema_and_privileges_match": True,
        "file_digests_match": True,
        "audit_valid": audit_result["valid"],
        "audit_events": audit_result["count"],
        "copied_sessions_revoked": sessions,
        "seconds": round(time.monotonic() - started, 2),
    }
    private_write(private / "restore-result.json", canonical(report) + b"\n")
    return report


def verify_source(archive, key, source, expected):
    with tempfile.TemporaryDirectory(dir="/work") as work:
        temp = Path(work)
        manifest = decrypt(archive, key, temp, expected)
        with connect(env_from(source / ".env")) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            if fingerprints(conn) != manifest["fingerprints"]:
                raise ValueError("source_changed_since_snapshot")
        for name, relative in SECRET_FILES.items():
            if (
                name in manifest["files"]
                and file_hash(source / relative) != manifest["files"][name]
            ):
                raise ValueError("source_secret_changed_since_snapshot")
    return {"operation": "verify-source", "result": "unchanged"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=["keygen", "create", "prepare", "restore", "verify-source"]
    )
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("/source"))
    parser.add_argument("--output", type=Path, default=Path("/backup"))
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--private", type=Path, default=Path("/private"))
    parser.add_argument("--sha256")
    parser.add_argument("--project")
    parser.add_argument("--app-image")
    parser.add_argument("--web-image")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.operation == "keygen":
            result = new_key(args.key)
        elif args.operation == "create":
            if not all(
                re.fullmatch(r"sha256:[a-f0-9]{64}", v or "")
                for v in (args.app_image, args.web_image)
            ):
                raise ValueError("pinned_images_required")
            result = create(args.source, args.output, args.key, args.app_image, args.web_image)
        else:
            if not args.archive or not re.fullmatch(r"[a-fA-F0-9]{64}", args.sha256 or ""):
                raise ValueError("archive_and_trusted_sha256_required")
            if args.operation == "verify-source":
                result = verify_source(args.archive, args.key, args.source, args.sha256)
            elif args.operation == "prepare":
                result = prepare(
                    args.archive, args.key, args.private, args.sha256, args.project or ""
                )
            else:
                result = restore(args.archive, args.key, args.private, args.sha256)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        safe_codes = {
            "restore_target_is_not_isolated_lab",
            "restore_target_is_not_empty",
            "restore_guard_mismatch",
            "restored_data_or_privileges_mismatch",
            "encrypted_checksum_mismatch",
            "invalid_archive_members",
            "archive_checksum_mismatch",
            "operator_command_failed",
            "source_changed_since_snapshot",
            "source_secret_changed_since_snapshot",
            "backup_exceeds_tmpfs_limit",
            "audit_key_or_chain_invalid",
        }
        print(
            json.dumps(
                {
                    "operation": args.operation,
                    "result": "failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error)
                    if str(error) in safe_codes
                    else "backup_operation_failed",
                }
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
