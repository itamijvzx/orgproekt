from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    app_mode: str = "development"
    app_origin: str = "http://localhost:8080"
    display_timezone: str = "Asia/Almaty"
    database_url: SecretStr
    broker_url: SecretStr
    mfa_encryption_key: SecretStr
    audit_hmac_key: SecretStr
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""
    gemini_enabled: bool = False
    session_hours: int = 8
    passkeys_enabled: bool = False
    local_demo_mode: bool = False

    @model_validator(mode="after")
    def validate_security(self):
        Fernet(self.mfa_encryption_key.get_secret_value().encode())
        ZoneInfo(self.display_timezone)
        if len(self.audit_hmac_key.get_secret_value()) < 32:
            raise ValueError("AUDIT_HMAC_KEY must contain at least 32 random characters")
        origin = urlparse(self.app_origin)
        if self.local_demo_mode and (
            self.app_mode not in {"development", "test"}
            or origin.scheme != "http"
            or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
            or self.passkeys_enabled
            or self.gemini_enabled
        ):
            raise ValueError(
                "Local demo requires a loopback HTTP origin, no passkeys or external AI"
            )
        if self.passkeys_enabled and (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Passkeys require an explicit HTTPS origin")
        if self.app_mode not in {"development", "test"}:
            # Deployment will be enabled only with verified component TLS in stage 4.
            raise ValueError("Production deployment is not enabled in this stage")
        if origin.scheme == "http" and origin.hostname not in {
            "localhost",
            "127.0.0.1",
            "testserver",
            "::1",
        }:
            raise ValueError("HTTP is restricted to loopback development")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
