from datetime import datetime, time

import pytest

from bot.utils.datetime_utils import is_quiet_hours, next_every_n_days
from bot.utils.html_utils import html_escape
from bot.utils.pagination import paginate
from bot.utils.permissions import PERMISSION_KEYS
from bot.utils.validation import validate_int, validate_time_str


def test_html_escape():
    assert html_escape('<b>&"x"') == "&lt;b&gt;&amp;&quot;x&quot;"
    assert html_escape(None) == ""


def test_validate_int_bounds():
    assert validate_int("15", 5, 50) == 15
    with pytest.raises(ValueError):
        validate_int("100", 5, 50)
    with pytest.raises(ValueError):
        validate_int("abc", 5, 50)


def test_validate_time():
    assert validate_time_str("09:30") == time(9, 30)
    with pytest.raises(ValueError):
        validate_time_str("25:00")


def test_paginate():
    items = list(range(37))
    page, total = paginate(items, page=3, page_size=15)
    assert total == 3 and page == list(range(30, 37))
    page, _ = paginate(items, page=99, page_size=15)   # нормализация
    assert page == list(range(30, 37))


def test_next_every_n_days():
    nxt = next_every_n_days(datetime(2026, 7, 10, 9, 0), 2, time(9, 0), "Europe/Moscow")
    assert (nxt.day, nxt.hour) == (12, 9)


def test_quiet_hours_wrap_midnight():
    assert is_quiet_hours(datetime(2026, 7, 10, 23, 30), time(22, 0), time(8, 0))
    assert is_quiet_hours(datetime(2026, 7, 10, 6, 0), time(22, 0), time(8, 0))
    assert not is_quiet_hours(datetime(2026, 7, 10, 12, 0), time(22, 0), time(8, 0))


def test_permission_keys_closed_set():
    assert "settings.manage" in PERMISSION_KEYS and len(PERMISSION_KEYS) == 12
