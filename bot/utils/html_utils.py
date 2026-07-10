import html


def html_escape(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def bold(value: object) -> str:
    return f"<b>{html_escape(value)}</b>"


def code(value: object) -> str:
    return f"<code>{html_escape(value)}</code>"
