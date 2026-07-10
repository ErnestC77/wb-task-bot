from bot.config import Settings, get_settings


def _env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "42:abc")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "/tmp/sa.json")


def test_settings_only_secrets(monkeypatch):
    _env(monkeypatch)
    s = Settings()
    assert s.bot_token == "42:abc"
    assert s.log_level == "INFO"
    # бизнес-настроек в Settings нет — они в system_settings
    for forbidden in ("group_chat_id", "timezone", "remind_after_hours"):
        assert not hasattr(s, forbidden)


def test_get_settings_cached(monkeypatch):
    _env(monkeypatch)
    get_settings.cache_clear()
    assert get_settings() is get_settings()
