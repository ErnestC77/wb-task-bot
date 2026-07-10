from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


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
