from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebAdminSettings(BaseSettings):
    """Секреты и инфраструктура веб-админки — по аналогии с bot/config.py."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    webadmin_password: str
    client_password: str
    webadmin_secret_key: str
    # Secure-флаг сессионной cookie. True — безопасный дефолт для реального
    # деплоя за HTTPS; локально (plain http://localhost) переопределяется
    # через WEBADMIN_HTTPS_ONLY=false в .env, иначе браузер не сохранит cookie.
    webadmin_https_only: bool = True


@lru_cache
def get_webadmin_settings() -> WebAdminSettings:
    return WebAdminSettings()
