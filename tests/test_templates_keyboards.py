from datetime import datetime

from bot.database.models import ArticleCheckItem, TaskInstance, TaskQuestion, TaskStatus
from bot.keyboards.task_keyboards import TaskCb, keyboard_for_status
from bot.utils.message_templates import (
    render_approval_request,
    render_question_message,
    render_reminder,
    render_task_message,
)


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


def test_render_mentions_responsible_user_by_id():
    inst = make_inst(TaskStatus.CREATED)
    inst.responsible_telegram_id_snapshot = 555
    text = render_task_message(inst)
    assert 'href="tg://user?id=555"' in text
    assert ">Валя</a>" in text


def test_render_due_at_same_day_says_today():
    inst = make_inst(TaskStatus.CREATED)
    text = render_task_message(inst)
    assert "Срок: сегодня до 12:00" in text


def test_render_due_at_next_day_says_tomorrow():
    """due_time не задан в конфиге -> due_at = scheduled_at + 24ч (срок "сутки"),
    его дата всегда на день позже scheduled_at — раньше текст всё равно писал
    "сегодня", что вводило пользователей в заблуждение."""
    inst = make_inst(TaskStatus.CREATED)
    inst.due_at = datetime(2026, 7, 11, 9)
    text = render_task_message(inst)
    assert "Срок: завтра до 09:00" in text
    assert "сегодня" not in text


def test_render_due_at_far_future_shows_date():
    inst = make_inst(TaskStatus.CREATED)
    inst.due_at = datetime(2026, 7, 15, 18, 0)
    text = render_task_message(inst)
    assert "Срок: до 15.07 18:00" in text


def test_render_reminder_escapes_html():
    inst = make_inst(TaskStatus.CREATED)
    inst.title_snapshot = "<script>x</script>"
    text = render_reminder(inst, "Напоминание: {title}")
    assert "&lt;script&gt;" in text and "<script>x</script>" not in text


def test_render_approval_request_escapes_html():
    inst = make_inst(TaskStatus.CREATED)
    inst.responsible_name_snapshot = "<script>x</script>"
    text = render_approval_request(inst)
    assert "&lt;script&gt;" in text and "<script>x</script>" not in text


def test_render_question_message_escapes_html():
    inst = make_inst(TaskStatus.CREATED)
    inst.title_snapshot = "<b>тест</b>"
    q = TaskQuestion(
        id=1, task_instance_id=1, from_user_id=1, to_user_id=2,
        question_text="<script>x</script>", created_at=datetime(2026, 7, 10, 9))
    item = ArticleCheckItem(
        id=1, check_session_id=1,
        article_snapshot="<b>art</b>", product_name_snapshot="<i>prod</i>")

    text = render_question_message(q, inst, item=item)

    assert "&lt;script&gt;" in text and "<script>x</script>" not in text
    assert "&lt;b&gt;" in text and "<b>тест</b>" not in text and "<b>art</b>" not in text
    assert "&lt;i&gt;" in text and "<i>prod</i>" not in text


def test_callback_data_compact():
    cb = TaskCb(a="start", i=123).pack()
    assert len(cb) <= 64                       # лимит Telegram
