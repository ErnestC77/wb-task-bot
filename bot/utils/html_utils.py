import html


def html_escape(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def bold(value: object) -> str:
    return f"<b>{html_escape(value)}</b>"


def code(value: object) -> str:
    return f"<code>{html_escape(value)}</code>"


def mention(telegram_id: int, name: object) -> str:
    """Кликабельное упоминание через tg://user?id= — работает даже если у
    пользователя не настроен публичный @username (в отличие от текстового
    @username, который резолвится только при наличии публичного имени)."""
    return f'<a href="tg://user?id={telegram_id}">{html_escape(name)}</a>'
