from datetime import datetime

from bot.database.models import TaskInstance, TaskStatus
from bot.keyboards.task_keyboards import TaskCb, keyboard_for_status
from bot.utils.message_templates import render_task_message


def make_inst(status, scenario="article_check"):
    return TaskInstance(
        id=1, config_id=1, status=status, scheduled_at=datetime(2026, 7, 10, 9),
        scheduled_date=datetime(2026, 7, 10).date(), schedule_key="k",
        title_snapshot="Проверить <b>все</b> артикулы",
        responsible_name_snapshot="Валя", due_at=datetime(2026, 7, 10, 12),
        scenario_snapshot=scenario)


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def test_article_check_buttons_created():
    kb = keyboard_for_status(make_inst(TaskStatus.CREATED))
    assert _texts(kb) == ["🔄 Начать проверку", "⏳ Перенести на завтра", "❓ Есть вопрос"]
    assert all("Есть проблема" not in t for t in _texts(kb))   # старой кнопки нет


def test_article_check_buttons_in_progress():
    kb = keyboard_for_status(make_inst(TaskStatus.IN_PROGRESS))
    assert "✅ Завершить проверку" in _texts(kb)


def test_no_buttons_for_terminal_and_waiting():
    for st in (TaskStatus.APPROVED, TaskStatus.WAITING_APPROVAL, TaskStatus.CANCELLED):
        assert keyboard_for_status(make_inst(st)) is None


def test_render_escapes_html():
    text = render_task_message(make_inst(TaskStatus.CREATED))
    assert "&lt;b&gt;" in text and "<b>все</b>" not in text
    assert "Валя" in text and "12:00" in text


def test_callback_data_compact():
    cb = TaskCb(a="start", i=123).pack()
    assert len(cb) <= 64                       # лимит Telegram
