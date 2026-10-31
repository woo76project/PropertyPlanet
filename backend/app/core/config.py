from functools import lru_cache
from decimal import Decimal
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# BaseSettings reads environment variables and the .env file. 
class Settings(BaseSettings):
    app_name: str = "Property Planets V3 API"
    db_url: SecretStr #prevent accidental exposure of sensitive information in logs or error messages.
    clerk_secret_key: SecretStr | None = None
    frontend_url: str = "http://localhost:3000"
    whatsapp_number: str | None = None
    credit_price_myr: Decimal = Field(default=Decimal("1.00"), gt=0, decimal_places=2)
    import_storage_dir: Path = Path(__file__).resolve().parents[2] / ".import_uploads"
    import_storage_bucket: str | None = None
    import_processor_enabled: bool = False

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value):
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.fullmatch(r"[1-9][0-9]{6,14}", value):
            raise ValueError("WHATSAPP_NUMBER needs international digits including country code, without + or spaces")
        return value

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        origin = urlsplit(value)
        if (origin.scheme not in ("http", "https") or not origin.hostname
                or origin.username is not None or origin.password is not None
                or origin.path or origin.query or origin.fragment):
            raise ValueError("FRONTEND_URL must be an http(s) origin, such as http://localhost:3000")
        # Accessing port also rejects invalid/non-numeric ports at configuration time.
        port = origin.port
        host = origin.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if port is not None and port != (443 if origin.scheme == "https" else 80):
            host += f":{port}"
        return f"{origin.scheme}://{host}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore obsolete V2 provider keys in copied local env files.
    )

#lru_cache lets us reuse the settings object instead of reading it repeatedly.
@lru_cache
def get_settings() -> Settings:
    return Settings()
