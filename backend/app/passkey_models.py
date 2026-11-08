from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import Identified, now


class Passkey(Identified, Base):
    __tablename__ = "passkeys"
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id"), index=True)
    rp_id: Mapped[str] = mapped_column(String(253))
    name: Mapped[str] = mapped_column(String(80))
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    # WebAuthn attested credential metadata and COSE public key.
    public_data: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasskeyChallenge(Base):
    __tablename__ = "passkey_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(12))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("browser_sessions.id", ondelete="CASCADE")
    )
    name: Mapped[str | None] = mapped_column(String(80))
    origin: Mapped[str] = mapped_column(String(320))
    state: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
