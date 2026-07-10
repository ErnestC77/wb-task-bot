from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ТОЛЬКО секреты и инфраструктура. Бизнес-настройки — в system_settings (БД)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    database_url: str
    google_sheets_credentials_file: str
    google_sheets_spreadsheet_id: str = ""
    encryption_key: str = ""
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
