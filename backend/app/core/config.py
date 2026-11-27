from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# BaseSettings reads environment variables and the .env file. 
class Settings(BaseSettings):
    app_name: str = "Property Planets V3 API"
    db_url: SecretStr #prevent accidental exposure of sensitive information in logs or error messages.
    clerk_secret_key: SecretStr | None = None
    frontend_url: str = "http://localhost:3000"
    import_storage_dir: Path = Path(__file__).resolve().parents[2] / ".import_uploads"
    import_storage_bucket: str | None = None
    import_processor_enabled: bool = False

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
