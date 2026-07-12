from datetime import date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.handlers.callbacks import ReturnCommentStates, handle_postpone
from bot.keyboards.approval_keyboards import ApproveCb
from bot.keyboards.task_keyboards import TaskCb
from bot.states.article_check_states import ArticleActionStates
from bot.states.question_states import QuestionStates
from tests.test_article_check_service import seed


def _state_for(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


def _denied(sender: AsyncMock, answer_attr: str = "answer") -> None:
    ans = getattr(sender, answer_attr)
    ans.assert_awaited()
    args, kwargs = ans.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert any(w in text.lower() for w in ("прав", "ответствен", "owner/partner"))


# ---------------------------------------------------------------------------
# Брифовые тесты (Step 1)
# ---------------------------------------------------------------------------

async def test_postpone_by_responsible(session):
    inst, valya, _ = await seed(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_postpone(callback, TaskCb(a="postpone", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.POSTPONED


async def test_postpone_by_stranger_rejected(session):
    inst, _, owner = await seed(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id           # owner не ответственный
    await handle_postpone(callback, TaskCb(a="postpone", i=inst.id), session)
    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.CREATED             # не изменилось


async def test_double_postpone_idempotent(session):
    inst, valya, _ = await seed(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    cb = TaskCb(a="postpone", i=inst.id)
    await handle_postpone(callback, cb, session)
    await handle_postpone(callback, cb, session)        # повторный callback
    from sqlalchemy import func, select
    from bot.database.models import TaskLog
    n = await session.scalar(select(func.count(TaskLog.id)))
    assert n == 1                                       # один переход, не два


def test_no_problem_button_handlers():
    """Старой механики «Есть проблема» больше нет ни в кнопках, ни в handlers."""
    import inspect
    import bot.handlers.callbacks as cb
    import bot.keyboards.task_keyboards as kb
    source = inspect.getsource(cb) + inspect.getsource(kb)
    assert "Есть проблема" not in source
    assert "problem" not in source.lower().replace("waiting_approval", "")


# ---------------------------------------------------------------------------
# Postpone: unregistered stranger (defense-in-depth beyond brief's literal set)
# ---------------------------------------------------------------------------

async def test_postpone_unregistered_user_rejected(session):
    inst, _valya, _owner = await seed(session)
    callback = AsyncMock()
    callback.from_user.id = 424242
    await handle_postpone(callback, TaskCb(a="postpone", i=inst.id), session)
    _denied(callback)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.CREATED


async def test_postpone_missing_instance_no_crash(session):
    inst, valya, _ = await seed(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_postpone(callback, TaskCb(a="postpone", i=999999), session)
    callback.answer.assert_awaited()   # "Уже обработано" (repo returns None) — no crash


# ---------------------------------------------------------------------------
# TaskCb(a="done") — simple-сценарий
# ---------------------------------------------------------------------------

async def _seed_simple(session, need_approval=False):
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=11, name="Валя", role=Role.MANAGER_WB)
    owner = await users.upsert(telegram_id=2, name="O", role=Role.OWNER)
    repo = TaskRepository(session)
    cfg = await repo.upsert_config(dict(
        external_task_id="simple_task", title="Простая задача",
        scenario="simple", schedule_type="daily",
        responsible_user_id=valya.id, need_approval=need_approval, is_active=True))
    inst = await repo.create_instance_idempotent(
        cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 18),
        dict(title_snapshot="Простая задача", scenario_snapshot="simple",
             need_approval_snapshot=need_approval, approval_timeout_hours_snapshot=24,
             responsible_name_snapshot="Валя"))
    await repo.transition_status(inst.id, [TaskStatus.CREATED], TaskStatus.IN_PROGRESS,
                                 valya.id, "btn:start")
    await session.commit()
    return inst, valya, owner


async def test_done_by_responsible_completes_without_approval(session):
    from bot.handlers.callbacks import handle_done
    inst, valya, _ = await _seed_simple(session, need_approval=False)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_done(callback, TaskCb(a="done", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.COMPLETED
    callback.answer.assert_awaited()


async def test_done_with_approval_goes_to_waiting_approval(session):
    from bot.handlers.callbacks import handle_done
    inst, valya, _ = await _seed_simple(session, need_approval=True)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_done(callback, TaskCb(a="done", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL


async def test_done_by_stranger_rejected(session):
    from bot.handlers.callbacks import handle_done
    inst, _, owner = await _seed_simple(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_done(callback, TaskCb(a="done", i=inst.id), session)
    _denied(callback)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.IN_PROGRESS


async def test_done_unregistered_user_rejected(session):
    from bot.handlers.callbacks import handle_done
    inst, _valya, _owner = await _seed_simple(session)
    callback = AsyncMock()
    callback.from_user.id = 555555
    await handle_done(callback, TaskCb(a="done", i=inst.id), session)
    _denied(callback)


async def test_done_missing_instance_no_crash(session):
    from bot.handlers.callbacks import handle_done
    _inst, valya, _owner = await _seed_simple(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_done(callback, TaskCb(a="done", i=999999), session)
    callback.answer.assert_awaited()


# ---------------------------------------------------------------------------
# TaskCb(a="finish") — article_check
# ---------------------------------------------------------------------------

async def _finish_ready_check(session):
    """Стартует проверку и отмечает все артикулы, чтобы finish_check прошёл."""
    from bot.services.article_check_service import ArticleCheckService
    from bot.database.models import CheckStatus
    from bot.database.repositories.article_check_repository import ArticleCheckRepository

    inst, valya, owner = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    items = await chk.get_items_for_batch(s.id, 1, 15)
    for item in items:
        await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, valya)
    await session.commit()
    return inst, valya, owner


async def test_finish_by_responsible_completes_check(session):
    from bot.handlers.callbacks import handle_finish
    inst, valya, _ = await _finish_ready_check(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_finish(callback, TaskCb(a="finish", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL   # seed() задаёт need_approval=True
    callback.answer.assert_awaited_with("Проверка завершена ✅")


async def test_finish_by_stranger_rejected(session):
    from bot.handlers.callbacks import handle_finish
    inst, _valya, owner = await _finish_ready_check(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_finish(callback, TaskCb(a="finish", i=inst.id), session)
    _denied(callback)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.IN_PROGRESS


async def test_finish_unregistered_user_rejected(session):
    from bot.handlers.callbacks import handle_finish
    inst, _valya, _owner = await _finish_ready_check(session)
    callback = AsyncMock()
    callback.from_user.id = 777777
    await handle_finish(callback, TaskCb(a="finish", i=inst.id), session)
    _denied(callback)


async def test_finish_missing_instance_no_crash(session):
    from bot.handlers.callbacks import handle_finish
    _inst, valya, _owner = await _finish_ready_check(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_finish(callback, TaskCb(a="finish", i=999999), session)
    callback.answer.assert_awaited()


# ---------------------------------------------------------------------------
# ApproveCb(a="ok") / (a="back") + ReturnCommentStates
# ---------------------------------------------------------------------------

async def _waiting_approval_task(session):
    inst, valya, owner = await _finish_ready_check(session)
    from bot.handlers.callbacks import handle_finish
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await handle_finish(callback, TaskCb(a="finish", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL
    return inst, valya, owner


async def test_approve_by_owner_ok(session):
    from bot.handlers.callbacks import handle_approve
    inst, _valya, owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.APPROVED


async def test_approve_by_owner_removes_buttons_from_message(session):
    from bot.handlers.callbacks import handle_approve
    inst, _valya, owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.message.html_text = "Задача выполнена и ждет подтверждения"
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    callback.message.edit_text.assert_awaited_once()
    args, kwargs = callback.message.edit_text.await_args
    text = args[0] if args else kwargs.get("text", "")
    assert "Подтверждено" in text
    assert kwargs.get("reply_markup") is None


async def test_approve_by_non_approver_rejected(session):
    from bot.handlers.callbacks import handle_approve
    inst, valya, _owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id           # исполнитель, не подтверждающий
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    _denied(callback)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL


async def test_approve_unregistered_user_rejected(session):
    from bot.handlers.callbacks import handle_approve
    inst, _valya, _owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = 888888
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    _denied(callback)


async def test_double_approve_idempotent(session):
    from bot.handlers.callbacks import handle_approve
    inst, _valya, owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    await handle_approve(callback, ApproveCb(a="ok", i=inst.id), session)
    assert callback.answer.await_args.args[0] == "Уже обработано"


async def test_return_request_starts_fsm_then_returns_to_work(session):
    from bot.handlers.callbacks import handle_return_comment, handle_return_request
    inst, _valya, owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    state = _state_for(owner.telegram_id)
    await handle_return_request(callback, ApproveCb(a="back", i=inst.id), session, state)
    assert await state.get_state() == ReturnCommentStates.waiting.state
    data = await state.get_data()
    assert data["instance_id"] == inst.id

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "Проверьте артикул 12345 ещё раз"
    await handle_return_comment(message, session, state)
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.IN_PROGRESS
    assert await state.get_state() is None            # состояние очищено


async def test_return_to_work_removes_buttons_from_approval_message(session):
    from bot.handlers.callbacks import handle_return_comment, handle_return_request
    inst, _valya, owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.message.chat.id = -100
    callback.message.message_id = 42
    callback.message.html_text = "Задача выполнена и ждет подтверждения"
    state = _state_for(owner.telegram_id)
    await handle_return_request(callback, ApproveCb(a="back", i=inst.id), session, state)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "Проверьте ещё раз"
    await handle_return_comment(message, session, state)

    message.bot.edit_message_text.assert_awaited_once()
    _, kwargs = message.bot.edit_message_text.await_args
    assert kwargs.get("chat_id") == -100 and kwargs.get("message_id") == 42
    assert "Возвращено в работу" in kwargs.get("text", "")
    assert kwargs.get("reply_markup") is None


async def test_return_request_by_non_approver_rejected(session):
    from bot.handlers.callbacks import handle_return_request
    inst, valya, _owner = await _waiting_approval_task(session)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(valya.telegram_id)
    await handle_return_request(callback, ApproveCb(a="back", i=inst.id), session, state)
    _denied(callback)
    assert await state.get_state() is None


async def test_return_comment_unregistered_user_rejected_without_crash(session):
    """Regression: буквальный код брифа падал бы AttributeError на None.role
    внутри ApprovalService.can_approve, если бы actor был None."""
    from bot.handlers.callbacks import handle_return_comment
    inst, _valya, owner = await _waiting_approval_task(session)
    state = _state_for(999999)
    await state.set_state(ReturnCommentStates.waiting)
    await state.update_data(instance_id=inst.id)
    message = AsyncMock()
    message.from_user.id = 999999                      # не зарегистрирован
    message.text = "комментарий"
    await handle_return_comment(message, session, state)
    _denied(message, "answer")
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL    # без изменений
    assert await state.get_state() is None


# ---------------------------------------------------------------------------
# /cancel — универсальная очистка ЛЮБОГО активного FSM (тест 9 раздела 14)
# ---------------------------------------------------------------------------

async def test_cancel_clears_article_action_fsm():
    from bot.handlers.start import cmd_cancel
    state = _state_for(1)
    await state.set_state(ArticleActionStates.comment)
    await state.update_data(item_id=42)
    message = AsyncMock()
    message.from_user.id = 1
    await cmd_cancel(message, state)
    assert await state.get_state() is None
    assert await state.get_data() == {}
    message.answer.assert_awaited_with("Действие отменено")


async def test_cancel_clears_question_fsm():
    from bot.handlers.start import cmd_cancel
    state = _state_for(2)
    await state.set_state(QuestionStates.waiting_text)
    message = AsyncMock()
    message.from_user.id = 2
    await cmd_cancel(message, state)
    assert await state.get_state() is None


async def test_cancel_clears_return_comment_fsm():
    from bot.handlers.start import cmd_cancel
    state = _state_for(3)
    await state.set_state(ReturnCommentStates.waiting)
    message = AsyncMock()
    message.from_user.id = 3
    await cmd_cancel(message, state)
    assert await state.get_state() is None


async def test_cancel_noop_without_active_state():
    from bot.handlers.start import cmd_cancel
    state = _state_for(4)
    message = AsyncMock()
    message.from_user.id = 4
    await cmd_cancel(message, state)          # не должно бросать
    assert await state.get_state() is None


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def test_start_registered_user_marks_private_chat(session):
    from bot.handlers.start import cmd_start
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=50, name="Валя", role=Role.MANAGER_WB)
    await session.commit()
    assert valya.private_chat_available is False

    message = AsyncMock()
    message.from_user.id = 50
    message.chat.type = "private"
    await cmd_start(message, session)

    refreshed = await users.get_by_telegram_id(50)
    assert refreshed.private_chat_available is True
    message.answer.assert_awaited()
    text = message.answer.await_args.args[0]
    assert "Валя" in text


async def test_start_unregistered_user_no_crash_no_mutation(session):
    from bot.handlers.start import cmd_start
    message = AsyncMock()
    message.from_user.id = 60
    message.chat.type = "private"
    await cmd_start(message, session)          # actor is None — ожидаемо, не падаем
    message.answer.assert_awaited()
    got = await UserRepository(session).get_by_telegram_id(60)
    assert got is None                          # /start не создаёт пользователей


async def test_start_in_group_chat_does_not_mark_private(session):
    from bot.handlers.start import cmd_start
    users = UserRepository(session)
    owner = await users.upsert(telegram_id=70, name="O", role=Role.OWNER)
    await session.commit()

    message = AsyncMock()
    message.from_user.id = 70
    message.chat.type = "group"
    await cmd_start(message, session)

    refreshed = await users.get_by_telegram_id(70)
    assert refreshed.private_chat_available is False


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

async def test_help_lists_commands(session):
    from bot.handlers.start import cmd_help
    message = AsyncMock()
    message.from_user.id = 999
    await cmd_help(message, session)
    text = message.answer.await_args.args[0]
    for cmd in ("/today", "/my_tasks", "/overdue", "/cancel"):
        assert cmd in text


# ---------------------------------------------------------------------------
# /today, /my_tasks, /overdue
# ---------------------------------------------------------------------------

async def _seed_two_employees(session):
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=100, name="Валя", role=Role.MANAGER_WB)
    kirill = await users.upsert(telegram_id=101, name="Кирилл", role=Role.LOGISTIC)
    owner = await users.upsert(telegram_id=102, name="O", role=Role.OWNER)
    repo = TaskRepository(session)
    cfg1 = await repo.upsert_config(dict(
        external_task_id="t_valya", title="Задача Вали", scenario="simple",
        schedule_type="daily", responsible_user_id=valya.id, is_active=True))
    cfg2 = await repo.upsert_config(dict(
        external_task_id="t_kirill", title="Задача Кирилла", scenario="simple",
        schedule_type="daily", responsible_user_id=kirill.id, is_active=True))
    today = date(2026, 7, 10)
    inst_valya = await repo.create_instance_idempotent(
        cfg1, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 18),
        dict(title_snapshot="Задача Вали", scenario_snapshot="simple",
             responsible_name_snapshot="Валя"))
    inst_kirill = await repo.create_instance_idempotent(
        cfg2, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 18),
        dict(title_snapshot="Задача Кирилла", scenario_snapshot="simple",
             responsible_name_snapshot="Кирилл"))
    await session.commit()
    assert inst_valya.scheduled_date == today and inst_kirill.scheduled_date == today
    return valya, kirill, owner, inst_valya, inst_kirill


def _pin_today(monkeypatch, dt: datetime) -> None:
    """Детерминированно фиксирует now_tz внутри bot.handlers.tasks — /today
    иначе зависит от реальной календарной даты запуска тестов (см. паттерн
    test_task_service._pin_now)."""
    import bot.handlers.tasks as tasks_module
    monkeypatch.setattr(tasks_module, "now_tz",
                        lambda tz_name: dt.replace(tzinfo=ZoneInfo(tz_name)))


async def test_today_shows_only_own_tasks_for_employee(session, monkeypatch):
    from bot.handlers.tasks import cmd_today
    _pin_today(monkeypatch, datetime(2026, 7, 10, 10, 0))
    valya, _kirill, _owner, inst_valya, inst_kirill = await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    await cmd_today(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text
    assert "Задача Кирилла" not in text


async def test_today_shows_all_tasks_for_owner(session, monkeypatch):
    from bot.handlers.tasks import cmd_today
    _pin_today(monkeypatch, datetime(2026, 7, 10, 10, 0))
    _valya, _kirill, owner, _iv, _ik = await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    await cmd_today(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text and "Задача Кирилла" in text


async def test_today_unregistered_user_rejected(session, monkeypatch):
    from bot.handlers.tasks import cmd_today
    _pin_today(monkeypatch, datetime(2026, 7, 10, 10, 0))
    await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = 999999
    await cmd_today(message, session)
    _denied(message, "answer")


async def test_my_tasks_employee_sees_only_own_open_tasks(session):
    from bot.handlers.tasks import cmd_my_tasks
    valya, _kirill, _owner, _iv, _ik = await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    await cmd_my_tasks(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text
    assert "Задача Кирилла" not in text


async def test_my_tasks_owner_sees_all_open_tasks(session):
    from bot.handlers.tasks import cmd_my_tasks
    _valya, _kirill, owner, _iv, _ik = await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    await cmd_my_tasks(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text and "Задача Кирилла" in text


async def test_overdue_employee_sees_only_own(session):
    from bot.handlers.tasks import cmd_overdue
    valya, _kirill, _owner, inst_valya, inst_kirill = await _seed_two_employees(session)
    repo = TaskRepository(session)
    await repo.transition_status(inst_valya.id, [TaskStatus.CREATED], TaskStatus.OVERDUE,
                                 None, "auto:overdue")
    await repo.transition_status(inst_kirill.id, [TaskStatus.CREATED], TaskStatus.OVERDUE,
                                 None, "auto:overdue")
    await session.commit()
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    await cmd_overdue(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text
    assert "Задача Кирилла" not in text


async def test_overdue_owner_sees_all(session):
    from bot.handlers.tasks import cmd_overdue
    _valya, _kirill, owner, inst_valya, inst_kirill = await _seed_two_employees(session)
    repo = TaskRepository(session)
    await repo.transition_status(inst_valya.id, [TaskStatus.CREATED], TaskStatus.OVERDUE,
                                 None, "auto:overdue")
    await repo.transition_status(inst_kirill.id, [TaskStatus.CREATED], TaskStatus.OVERDUE,
                                 None, "auto:overdue")
    await session.commit()
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    await cmd_overdue(message, session)
    text = message.answer.await_args.args[0]
    assert "Задача Вали" in text and "Задача Кирилла" in text


async def test_overdue_empty_list_message(session):
    from bot.handlers.tasks import cmd_overdue
    valya, _kirill, _owner, _iv, _ik = await _seed_two_employees(session)
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    await cmd_overdue(message, session)
    text = message.answer.await_args.args[0]
    assert "Нет задач" in text
