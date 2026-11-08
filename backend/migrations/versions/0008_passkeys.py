"""Optional WebAuthn credentials and one-time browser challenges."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE passkeys (
        id varchar(36) PRIMARY KEY, user_id varchar(36) NOT NULL REFERENCES user_accounts(id),
        rp_id varchar(253) NOT NULL, name varchar(80) NOT NULL, credential_id bytea UNIQUE NOT NULL,
        public_data bytea NOT NULL, sign_count bigint NOT NULL CHECK (sign_count >= 0),
        installed_at timestamptz NOT NULL, last_used_at timestamptz, revoked_at timestamptz
    )""")
    op.execute("CREATE INDEX ix_passkeys_user_id ON passkeys(user_id)")
    op.execute("""CREATE TABLE passkey_challenges (
        token_hash varchar(64) PRIMARY KEY, purpose varchar(12) NOT NULL,
        session_id varchar(36) REFERENCES browser_sessions(id) ON DELETE CASCADE, name varchar(80),
        origin varchar(320) NOT NULL, state jsonb NOT NULL, expires_at timestamptz NOT NULL
    )""")
    op.execute("CREATE INDEX ix_passkey_challenges_expires_at ON passkey_challenges(expires_at)")
    op.execute("REVOKE ALL ON passkeys, passkey_challenges FROM orgproekt_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE(sign_count, last_used_at, revoked_at) ON passkeys TO orgproekt_app"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON passkey_challenges TO orgproekt_app")


def downgrade():
    raise RuntimeError("Restore a verified backup to retain the credential history.")
