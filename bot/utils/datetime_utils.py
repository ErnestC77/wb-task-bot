from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MOSCOW_UTC_OFFSET_HOURS = 3


def moscow_to_utc(t: time | None) -> time | None:
    """Планировщик (scheduler_service.py) целиком работает в naive-UTC —
    любое время, введённое человеком по московскому времени (МСК = UTC+3,
    без перехода на летнее/зимнее), обязано пройти через эту конвертацию
    перед записью в TaskConfig.time, иначе задача уйдёт на 3 часа позже
    задуманного. Не обрабатывает переход через полночь (сдвиг даты здесь
    взять негде — вызывающий код должен сам решать, нужен ли сдвиг дня)."""
    if t is None:
        return None
    return t.replace(hour=(t.hour - MOSCOW_UTC_OFFSET_HOURS) % 24)


def utc_to_moscow(t: time | None) -> time | None:
    """Обратная к moscow_to_utc — для отображения хранимого в UTC
    TaskConfig.time администратору по МСК, как он его вводил."""
    if t is None:
        return None
    return t.replace(hour=(t.hour + MOSCOW_UTC_OFFSET_HOURS) % 24)


def utc_dt_to_moscow(dt: datetime | None) -> datetime | None:
    """Как utc_to_moscow, но для полного datetime (next_run_at и т.п.) —
    timedelta сам корректно переносит дату при переходе через полночь,
    в отличие от time-only версии."""
    if dt is None:
        return None
    return dt + timedelta(hours=MOSCOW_UTC_OFFSET_HOURS)


def now_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def combine_local(day: date, t: time, tz_name: str) -> datetime:
    return datetime.combine(day, t, tzinfo=ZoneInfo(tz_name))


def next_every_n_days(prev: datetime, n: int, at_time: time, tz_name: str) -> datetime:
    next_day = (prev.date() + timedelta(days=n))
    return combine_local(next_day, at_time, tz_name).replace(tzinfo=None) \
        if prev.tzinfo is None else combine_local(next_day, at_time, tz_name)


def is_quiet_hours(dt: datetime, start: time, end: time) -> bool:
    t = dt.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end          # интервал через полночь


def shift_to_morning(dt: datetime, end: time) -> datetime:
    day = dt.date() if dt.time() < end else dt.date() + timedelta(days=1)
    return datetime.combine(day, end, tzinfo=dt.tzinfo)
