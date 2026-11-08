"""Real age encryption failures and fail-closed restore guards; no production database used."""

import hashlib
import io
import json
import shutil
import subprocess
import tarfile

import pytest

from ops import backup


def payload(path, extra=None, digest_ok=True):
    files = {"database.dump": b"PGDMP-test-only", "secrets/environment.env": b"SECRET=test-value"}
    manifest = {
        "format": 1,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    if not digest_ok:
        manifest["files"]["database.dump"] = "0" * 64
    with tarfile.open(path, "w") as tar:
        backup.add_bytes(tar, "manifest.json", json.dumps(manifest).encode())
        for name, data in files.items():
            backup.add_bytes(tar, name, data)
        if extra:
            tar.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    return manifest


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("database.dump", tarfile.REGTYPE),
        ("secrets/demo-accounts.json", tarfile.SYMTYPE),
        ("secrets/demo-accounts.json", tarfile.LNKTYPE),
        ("unexpected", tarfile.DIRTYPE),
    ],
)
def test_backup_rejects_archive_paths_duplicates_and_links(tmp_path, name, kind):
    member = tarfile.TarInfo(name)
    member.type, member.linkname = kind, "../../outside"
    member.size = 1 if member.isfile() else 0
    payload(tmp_path / "data.tar", member)
    with pytest.raises(ValueError, match="invalid_archive_members"):
        backup.unpack_checked(tmp_path / "data.tar", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_backup_rejects_inner_checksum_before_restore(tmp_path):
    payload(tmp_path / "data.tar", digest_ok=False)
    with pytest.raises(ValueError, match="checksum"):
        backup.unpack_checked(tmp_path / "data.tar", tmp_path / "out")


@pytest.mark.parametrize("failure", ["wrong_key", "changed", "truncated", "wrong_receipt"])
@pytest.mark.skipif(
    shutil.which("age") is None, reason="Real age cases run in backup tools CI image"
)
def test_backup_real_age_rejects_wrong_key_corruption_and_receipt(tmp_path, failure):
    key, other = tmp_path / "one.agekey", tmp_path / "two.agekey"
    backup.new_key(key)
    backup.new_key(other)
    payload(tmp_path / "data.tar")
    recipient = (
        backup.run(["age-keygen", "-y", str(key)], stdout=subprocess.PIPE).stdout.decode().strip()
    )
    encrypted = tmp_path / "data.age"
    backup.run(["age", "-r", recipient, "-o", str(encrypted), str(tmp_path / "data.tar")])
    expected = backup.file_hash(encrypted)
    if failure == "changed":
        data = bytearray(encrypted.read_bytes())
        data[-5] ^= 1
        encrypted.write_bytes(data)
        expected = backup.file_hash(encrypted)  # even if an attacker replaces the outer receipt
    elif failure == "truncated":
        encrypted.write_bytes(encrypted.read_bytes()[:-20])
        expected = backup.file_hash(encrypted)
    elif failure == "wrong_receipt":
        expected = "0" * 64
    with pytest.raises((ValueError, RuntimeError)):
        backup.decrypt(
            encrypted, other if failure == "wrong_key" else key, tmp_path / "out", expected
        )
    assert not (tmp_path / "out/verified").exists()


@pytest.mark.skipif(
    shutil.which("age") is None, reason="Real age cases run in backup tools CI image"
)
def test_backup_roundtrip_authenticates_before_consuming_and_key_not_overwritten(tmp_path):
    key = tmp_path / "one.agekey"
    backup.new_key(key)
    before = key.read_bytes()
    with pytest.raises(FileExistsError):
        backup.new_key(key)
    assert key.read_bytes() == before
    manifest = payload(tmp_path / "data.tar")
    recipient = (
        backup.run(["age-keygen", "-y", str(key)], stdout=subprocess.PIPE).stdout.decode().strip()
    )
    encrypted = tmp_path / "data.age"
    backup.run(["age", "-r", recipient, "-o", str(encrypted), str(tmp_path / "data.tar")])
    assert backup.decrypt(encrypted, key, tmp_path / "out", backup.file_hash(encrypted)) == manifest
    assert (tmp_path / "out/verified/secrets/environment.env").read_bytes() == b"SECRET=test-value"


@pytest.mark.parametrize("marker,count", [(None, 0), ("orgproekt", 0), ("correct", 1)])
def test_restore_refuses_main_or_nonempty_database(marker, count):
    project = "orgproekt-restore-20261031000000-0123abcd"

    class Query:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return (self.value,)

    class Connection:
        def execute(self, query):
            return Query(
                (project if marker == "correct" else marker)
                if "current_setting" in query
                else count
            )

    with pytest.raises(ValueError):
        backup.require_empty_lab(Connection(), project)


def test_operator_failure_never_prints_exception_secret(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr("sys.argv", ["backup", "keygen", "--key", str(tmp_path / "key")])

    def fail(_):
        raise ValueError("private-database-password")

    monkeypatch.setattr(backup, "new_key", fail)
    assert backup.main() == 1
    output = capsys.readouterr()
    assert "private-database-password" not in output.err + output.out
    assert json.loads(output.err)["error_type"] == "ValueError"


def test_restore_project_name_is_not_user_controlled_main_name():
    for value in ("orgproekt", "orgproekt-e2e", "../orgproekt", "orgproekt-restore-main"):
        assert not backup.RESTORE_ID.fullmatch(value)


def test_postgres_literal_array_roundtrip_preserves_other_check_semantics():
    before = "CHECK (((kind)::text = ANY ((ARRAY['index'::character varying, 'compare'::character varying])::text[])))"
    after = "CHECK (((kind)::text = ANY (ARRAY[('index'::character varying)::text, ('compare'::character varying)::text])))"
    assert backup.normalize_constraint(before) == backup.normalize_constraint(after)
    assert backup.normalize_constraint(
        after.replace("'index'", "'other'")
    ) != backup.normalize_constraint(before)
    for expression in (
        "CHECK (amount > 0)",
        "CHECK (amount >= 0)",
        "CHECK ((value::integer)::text='1')",
    ):
        assert backup.normalize_constraint(expression) == expression
