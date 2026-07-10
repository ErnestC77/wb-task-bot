import json
from datetime import time


def validate_int(raw: object, min_: int | None = None, max_: int | None = None) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("Введите целое число") from None
    if min_ is not None and value < min_:
        raise ValueError(f"Минимум: {min_}")
    if max_ is not None and value > max_:
        raise ValueError(f"Максимум: {max_}")
    return value


def validate_time_str(raw: str) -> time:
    try:
        h, m = raw.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        raise ValueError("Формат времени: ЧЧ:ММ, например 09:30") from None


def validate_choice(raw: str, choices: list[str]) -> str:
    if raw not in choices:
        raise ValueError("Допустимые значения: " + ", ".join(choices))
    return raw


def validate_json_value(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Некорректный JSON") from None
