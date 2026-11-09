"""Portable setup for CI/Linux; never prints or overwrites secrets."""
import base64
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    print("Existing .env preserved")
else:
    values = {
        "APP_MODE": "development",
        "LOCAL_DEMO_MODE": "true",
        "APP_ORIGIN": "http://orgproekt.localhost:8080",
        "DISPLAY_TIMEZONE": "Asia/Almaty",
        "DB_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "DB_APP_PASSWORD": secrets.token_urlsafe(32),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(32),
        "MFA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "AUDIT_HMAC_KEY": secrets.token_urlsafe(48),
        "GEMINI_API_KEY": "",
        "GEMINI_MODEL": "",
        "GEMINI_ENABLED": "false",
    }
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    path.chmod(0o600)
    (root / ".local").mkdir(exist_ok=True)
    print("Created local secrets; no values printed")
