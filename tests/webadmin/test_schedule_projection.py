from datetime import date, datetime, time

from bot.database.models import TaskConfig
from webadmin.schedule_projection import (
    month_range, project_occurrences, shift_period, week_range,
)


def cfg(**kw):
    base = dict(external_task_id="x", title="x", schedule_type="daily",
                time=time(9, 0), run_on_weekends=True, is_active=True)
    base.update(kw)
    return TaskConfig(**base)


def test_daily_occurs_every_day_in_range():
    c = cfg(schedule_type="daily")
    result = project_occurrences(c, date(2026, 7, 13), date(2026, 7, 15))
    assert result == [
        datetime(2026, 7, 13, 9, 0), datetime(2026, 7, 14, 9, 0), datetime(2026, 7, 15, 9, 0),
    ]


def test_daily_skips_weekends_when_disabled():
    c = cfg(schedule_type="daily", run_on_weekends=False)
    result = project_occurrences(c, date(2026, 7, 10), date(2026, 7, 13))  # Пт-Пн
    assert [d.date() for d in result] == [date(2026, 7, 10), date(2026, 7, 13)]


def test_every_n_days_uses_next_run_at_as_phase():
    c = cfg(schedule_type="every_n_days", schedule_interval=2,
            next_run_at=datetime(2026, 7, 15, 9, 0))
    result = project_occurrences(c, date(2026, 7, 15), date(2026, 7, 21))
    assert [d.date() for d in result] == [
        date(2026, 7, 15), date(2026, 7, 17), date(2026, 7, 19), date(2026, 7, 21),
    ]


def test_weekly_multiple_days():
    c = cfg(schedule_type="weekly", schedule_value="0,2,4", time=time(10, 0))  # пн/ср/пт
    result = project_occurrences(c, date(2026, 7, 13), date(2026, 7, 19))  # пн-вс
    assert [d.date() for d in result] == [date(2026, 7, 13), date(2026, 7, 15), date(2026, 7, 17)]


def test_monthly_clamps_to_month_end():
    c = cfg(schedule_type="monthly", schedule_value="31")
    result = project_occurrences(c, date(2026, 2, 1), date(2026, 2, 28))
    assert [d.date() for d in result] == [date(2026, 2, 28)]


def test_cron_matches_expression():
    c = cfg(schedule_type="cron", schedule_value="30 14 * * *")
    result = project_occurrences(c, date(2026, 7, 15), date(2026, 7, 16))
    assert result == [datetime(2026, 7, 15, 14, 30), datetime(2026, 7, 16, 14, 30)]


def test_inactive_config_returns_nothing():
    c = cfg(schedule_type="daily", is_active=False)
    assert project_occurrences(c, date(2026, 7, 13), date(2026, 7, 15)) == []


def test_week_range_monday_to_sunday():
    assert week_range(date(2026, 7, 15)) == (date(2026, 7, 13), date(2026, 7, 19))  # 15 июля — среда


def test_month_range_first_to_last_day():
    assert month_range(date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_shift_period_week_forward_and_back():
    start, end = date(2026, 7, 13), date(2026, 7, 19)
    assert shift_period(start, end, "week", 1) == (date(2026, 7, 20), date(2026, 7, 26))
    assert shift_period(start, end, "week", -1) == (date(2026, 7, 6), date(2026, 7, 12))


def test_shift_period_month_forward_and_back():
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    assert shift_period(start, end, "month", 1) == (date(2026, 8, 1), date(2026, 8, 31))
    assert shift_period(start, end, "month", -1) == (date(2026, 6, 1), date(2026, 6, 30))
