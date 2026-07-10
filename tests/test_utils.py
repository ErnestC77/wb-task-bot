from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from bot.utils.datetime_utils import (
    combine_local,
    is_quiet_hours,
    next_every_n_days,
    now_tz,
    shift_to_morning,
)
from bot.utils.html_utils import bold, code, html_escape
from bot.utils.pagination import paginate
from bot.utils.permissions import PERMISSION_KEYS, PERMISSION_TITLES
from bot.utils.validation import (
    validate_choice,
    validate_int,
    validate_json_value,
    validate_time_str,
)


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


def test_bold_wraps_and_escapes():
    assert bold('<x>&"y"') == '<b>&lt;x&gt;&amp;&quot;y&quot;</b>'


def test_code_wraps_and_escapes():
    assert code('<x>&"y"') == '<code>&lt;x&gt;&amp;&quot;y&quot;</code>'


def test_validate_choice():
    assert validate_choice("b", ["a", "b", "c"]) == "b"
    with pytest.raises(ValueError):
        validate_choice("z", ["a", "b", "c"])


def test_validate_json_value_valid():
    assert validate_json_value('{"a": 1}') == {"a": 1}
    assert validate_json_value("[1, 2, 3]") == [1, 2, 3]


def test_validate_json_value_invalid_string():
    with pytest.raises(ValueError):
        validate_json_value("{not valid json")


def test_validate_json_value_non_string_raises_value_error():
    with pytest.raises(ValueError):
        validate_json_value(None)  # type: ignore[arg-type]


def test_now_tz_returns_aware_datetime_with_expected_zone():
    dt = now_tz("Europe/Moscow")
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=3)


def test_combine_local():
    dt = combine_local(date(2026, 7, 10), time(9, 30), "Europe/Moscow")
    assert dt == datetime(2026, 7, 10, 9, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    assert dt.utcoffset() == timedelta(hours=3)


def test_shift_to_morning_before_end_stays_same_day():
    dt = datetime(2026, 7, 10, 5, 0)
    result = shift_to_morning(dt, time(8, 0))
    assert result == datetime(2026, 7, 10, 8, 0)


def test_shift_to_morning_after_end_moves_to_next_day():
    dt = datetime(2026, 7, 10, 10, 0)
    result = shift_to_morning(dt, time(8, 0))
    assert result == datetime(2026, 7, 11, 8, 0)


def test_paginate_empty_items():
    page, total = paginate([], page=1, page_size=10)
    assert page == [] and total == 1


def test_paginate_page_clamped_to_minimum():
    items = list(range(5))
    page_zero, total_zero = paginate(items, page=0, page_size=2)
    page_negative, total_negative = paginate(items, page=-5, page_size=2)
    assert page_zero == items[0:2] and total_zero == 3
    assert page_negative == items[0:2] and total_negative == 3


def test_paginate_zero_page_size_raises_value_error():
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page=1, page_size=0)


def test_permission_titles_cover_all_keys():
    for key in PERMISSION_KEYS:
        assert key in PERMISSION_TITLES
