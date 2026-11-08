"""Explicit restore-lab QA fixture: read cloned factors, never enroll/reset a real account.

Outputs ephemeral browser inputs ONLY to the private lab folder, not stdout.
Run before browser QA; delete qa-accounts.json immediately afterwards.
"""

import json
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet

from ops.backup import RESTORE_ID, canonical, connect, env_from, private_write, validate_secrets


def prepare_browser(private):
    config = env_from(private / "restore.env")
    guard = json.loads((private / "restore.guard.json").read_text())
    project = guard.get("project", "")
    if not RESTORE_ID.fullmatch(project) or project != config.get("RESTORE_ID"):
        raise ValueError("not_a_restore_lab")
    result = json.loads((private / "restore-result.json").read_text())
    if result.get("result") != "verified":
        raise ValueError("restore_not_verified")
    with connect(config) as conn:
        if (
            conn.execute("SELECT current_setting('orgproekt.restore_id',true)").fetchone()[0]
            != project
        ):
            raise ValueError("not_a_restore_database")
        if conn.execute("SELECT count(*) FROM browser_sessions WHERE NOT revoked").fetchone()[0]:
            raise ValueError("copied_sessions_not_revoked_or_lab_already_used")
        versions = dict(conn.execute("SELECT id,sha256 FROM document_versions").fetchall())
        blobs = [
            {"id": row[0], "pdf": row[1], "docx": row[2]}
            for row in conn.execute("SELECT report_id,pdf_sha256,docx_sha256 FROM report_releases")
        ]
        finance = conn.execute("SELECT id FROM departments WHERE code='FIN'").fetchone()[0]
        users = conn.execute("SELECT username,mfa_secret,active FROM user_accounts").fetchall()
        copied_sessions = conn.execute(
            "SELECT count(*) FROM browser_sessions WHERE revoked"
        ).fetchone()[0]
    credentials = {}
    for name in ("demo-accounts.json", "workflow-accounts.json", "report-reviewer.json"):
        if (private / name).exists():
            content = json.loads((private / name).read_text())
            credentials.update(content.get("accounts", content))
    accounts = {}
    for username, secret, active in users:
        if username in {"auditor", "it", "access"}:
            if not active or not secret or username not in credentials:
                raise ValueError("required_manual_test_accounts_not_ready")
            accounts[username] = {
                "password": credentials[username]["password"],
                "secret": Fernet(config["MFA_ENCRYPTION_KEY"].encode())
                .decrypt(secret.encode())
                .decode(),
            }
    if set(accounts) != {"auditor", "it", "access"}:
        raise ValueError("required_test_accounts_missing")
    # Check runtime SQL permissions independently of UI filtering.
    with psycopg.connect(
        host="db", dbname="orgproekt", user="orgproekt_app", password=config["DB_APP_PASSWORD"]
    ) as conn:
        if conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
            raise ValueError("runtime_superuser")
        try:
            conn.execute("UPDATE audit_events SET payload='{}'::jsonb")
        except psycopg.Error:
            conn.rollback()
        else:
            conn.rollback()
            raise ValueError("audit_update_not_protected")
    audit_result = validate_secrets(config)
    private_write(
        private / "qa-accounts.json",
        canonical(
            {
                "project": project,
                "accounts": accounts,
                "reports": blobs,
                "documents": versions,
                "foreign_department": finance,
            }
        ),
    )
    private_write(
        private / "qa-precheck.json",
        canonical(
            {
                "runtime_not_superuser": True,
                "audit_update_denied": True,
                "audit_valid": audit_result["valid"],
                "revoked_sessions": copied_sessions,
            }
        ),
    )
    return {
        "result": "ready",
        "accounts": len(accounts),
        "reports": len(blobs),
        "runtime_permissions": "verified",
    }


if __name__ == "__main__":
    try:
        print(json.dumps(prepare_browser(Path("/private"))))
    except Exception as error:
        print(json.dumps({"result": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
