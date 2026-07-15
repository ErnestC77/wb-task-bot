import calendar
from datetime import date, datetime, time, timedelta

from apscheduler.triggers.cron import CronTrigger

from bot.database.models import ScheduleType, TaskConfig


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def project_occurrences(config: TaskConfig, range_start: date, range_end: date) -> list[datetime]:
    """Все моменты, когда TaskConfig сработал бы в [range_start, range_end]
    (обе даты включительно) — только для отображения графика, ничего не
    создаёт и не меняет в БД. Считает по типу расписания напрямую (не через
    scheduler_service.compute_next_run — та функция односторонняя, "вперёд
    от anchor", и не годится для произвольного диапазона, включая прошлое).
    Для EVERY_N_DAYS единственный способ узнать фазу цикла без реального
    anchor — взять config.next_run_at (уже корректно посчитан планировщиком)
    как точку отсчёта; если его нет — created_at; если и его нет (например,
    ещё не сохранённый в БД TaskConfig с server_default) — range_start, чтобы
    хотя бы интервал был детерминированным в пределах всего вызова."""
    if not config.is_active:
        return []
    st = config.schedule_type
    t = config.time or time(9, 0)
    if st == ScheduleType.CRON:
        return _project_cron(config, range_start, range_end)
    result: list[datetime] = []
    for d in date_range(range_start, range_end):
        if not config.run_on_weekends and d.weekday() >= 5:
            continue
        if _matches(config, st, d, range_start):
            result.append(datetime.combine(d, t))
    return result


def _matches(config: TaskConfig, st: str, d: date, range_start: date) -> bool:
    if st == ScheduleType.DAILY:
        return True
    if st == ScheduleType.EVERY_N_DAYS:
        interval = config.schedule_interval or 1
        anchor = (config.next_run_at.date() if config.next_run_at
                  else (config.created_at.date() if config.created_at else range_start))
        return (d - anchor).days % interval == 0
    if st == ScheduleType.WEEKLY:
        targets = [int(v) for v in str(config.schedule_value or "0").split(",") if v.strip()]
        return d.weekday() in targets
    if st == ScheduleType.MONTHLY:
        target_day = int(config.schedule_value or 1)
        return d.day == min(target_day, calendar.monthrange(d.year, d.month)[1])
    return False


def _project_cron(config: TaskConfig, range_start: date, range_end: date) -> list[datetime]:
    trigger = CronTrigger.from_crontab(config.schedule_value or "0 9 * * *")
    cursor = datetime.combine(range_start - timedelta(days=1), time(23, 59, 59, 999999))
    previous = None
    result: list[datetime] = []
    for _ in range(400):  # предохранитель от зацикливания на битом выражении
        fire = trigger.get_next_fire_time(previous, cursor)
        if fire is None or fire.date() > range_end:
            break
        result.append(fire.replace(tzinfo=None))
        previous = fire
        cursor = fire
    return result


def week_range(ref: date) -> tuple[date, date]:
    start = ref - timedelta(days=ref.weekday())
    return start, start + timedelta(days=6)


def month_range(ref: date) -> tuple[date, date]:
    start = ref.replace(day=1)
    end = ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
    return start, end


def shift_period(start: date, end: date, period: str, direction: int) -> tuple[date, date]:
    if period == "week":
        delta = timedelta(days=7 * direction)
        return start + delta, end + delta
    anchor = (end + timedelta(days=1)) if direction > 0 else (start - timedelta(days=1))
    return month_range(anchor)
