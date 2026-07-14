"""Раздел админ-панели «▶ Ручной запуск» + «📋 Активные задачи» (Task 34,
все ручные операции раздела 4.15 плана).

Оба пункта меню («run» и «act», а также «bak» из будущего Task 35 — см.
`SECTION_ALIASES` в bot/keyboards/admin/main.py) ведут в ЭТОТ модуль и делят
одно право `tasks.run_manual` (`SECTION_PERMISSIONS["run"|"act"|"bak"]` через
alias -> "ops"). Этот файл реализует только «run» (список активных шаблонов
-> запустить через confirm_token) и «act» (список активных TaskInstance ->
карточка -> меню операций); «bak» («Резервные операции» — recover_scheduler/
recover_pending_deliveries) UI отложена до Task 35, но их ДОМЕННЫЕ функции
уже определены здесь по брифу Task 34.

«🔁 Переназначить» на карточке задачи ведёт НЕ в собственный экран, а
напрямую в уже одобренный флоу Task 25 (`UsrCb(a="reassign_pick", ...)`,
bot/handlers/admin/users.py) — брифовая аннотация "(→Task 25)" означает
переиспользовать существующий код, а не дублировать его.

Как и в разделах Tasks 25-33, навигация внутри списка/карточки использует
СОБСТВЕННЫЙ `OpsCb` (prefix="o"), НЕ `AdminCb` — каждый callback этого
модуля заново проверяет actor + право `tasks.run_manual`. FSM здесь не
нужен — все операции запускаются кнопками, ни одна не требует свободного
текстового ввода.

Отклонения от буквального кода брифа (Step 2), с обоснованием:
1. `resend_task_message` — брифовый код напрямую вызывает
   `DeliveryService.send_task_message(inst)`, но тот САМ содержит guard
   `if inst.delivery_status == SENT: return True` (не дублирует отправку) —
   для УЖЕ отправленной задачи buffer «Повторить отправку» тогда был бы
   молчаливым no-op, а брифовый же тест `test_resend_increments_delivery_attempt`
   явно ожидает `delivery_attempts == 2` после ДВУХ вызовов подряд (второй —
   когда статус уже SENT). Исправлено: перед вызовом `send_task_message`
   статус сбрасывается на PENDING, чтобы гарантировать реальную повторную
   отправку (в этом и есть смысл кнопки «повторить»).
2. `recalc_next_run` — брифовый код делает `await session.flush()` (НЕ
   commit) перед вызовом `scheduler_svc.rebuild_config_job(config_id)`, а
   `rebuild_config_job` (Task 12) открывает СОБСТВЕННУЮ сессию через
   `session_factory`. ВАЖНО (уточнение после ревью Task 34, чтобы не вводить
   в заблуждение): это НЕ тот же Postgres-баг, что Critical-фикс Task 27/28/33
   (`apply_schedule_field`/`apply_setting_input`) — там caller записывал
   именно те поля (schedule_type/interval/value/time, sync.auto_enabled/
   interval_minutes), которые rebuild-функция читает заново из своей сессии.
   Здесь `rebuild_config_job` НЕ читает `next_run_at` вообще — он всегда
   пересчитывает его самостоятельно через `compute_next_run` из полей
   расписания, которые `recalc_next_run` не трогает, так что на Postgres
   результат идентичен что при flush, что при commit. Причина исправления
   иная: без коммита СЕССИЯ (не поле) остаётся в открытой транзакции на
   SQLite/StaticPool в тестах, и `rebuild_config_job`'s `session_factory()`
   не может открыть свою (та же физическая коннекция, "cannot start a
   transaction within a transaction"). Коммит здесь — гигиена транзакции
   для теста, не обязательное условие корректности на Postgres, но и не
   вредит: делать его раньше по-прежнему безопасно.
3. `force_close` — брифовый код переводит статус в COMPLETED/CANCELLED, но
   не проставляет `completed_at`/`cancelled_at` (в отличие от ВСЕХ остальных
   путей перехода в эти статусы — см. `approval_service.py`/
   `article_check_service.py`, которые всегда передают `completed_at=...`
   через `**timestamps` у `transition_status`). Оставлять их `None` при
   принудительном закрытии — рассинхронизация с обычным путём. Исправлено:
   добавлены соответствующие timestamp'ы.
"""
from datetime import datetime

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from bot.database.models import DeliveryStatus, TaskInstance, TaskStatus
from bot.database.repositories.task_repository import OPEN_STATUSES, TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.operations import (
    OpsCb, active_list_keyboard, instance_card_keyboard, run_list_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.delivery_service import DeliveryService
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService
from bot.services.user_service import UserService
from bot.utils.html_utils import html_escape

router = Router(name=__name__)

PERMISSION = "tasks.run_manual"
ACTIVE_STATUSES = OPEN_STATUSES | {TaskStatus.WAITING_APPROVAL}


# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры
# и тела заданы брифом Task 34 (кроме трёх исправленных мест, см. docstring).
# --------------------------------------------------------------------------

async def manual_run_config(session, bot, actor, config_id: int,
                            at: datetime | None = None) -> bool:
    config = await TaskRepository(session).get_config(config_id)
    if config is None:
        raise ValueError("Шаблон не найден")
    scheduled_at = (at or datetime.utcnow()).replace(second=0, microsecond=0)
    inst = await TaskService(session, bot).create_instance_for(config, scheduled_at)
    await AuditService(session).log(actor.id, "task.manual_run",
                                    entity_type="task_config", entity_id=str(config_id),
                                    result="ok" if inst else "duplicate")
    if inst is None:
        return False
    await DeliveryService(session, bot).send_task_message(inst)
    return True


async def resend_task_message(session, bot, actor, instance_id: int) -> bool:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None:
        raise ValueError("Задача не найдена")
    # Форсируем реальную повторную отправку — send_task_message сам
    # пропускает уже-SENT инстансы (см. docstring модуля, п.1).
    inst.delivery_status = DeliveryStatus.PENDING
    ok = await DeliveryService(session, bot).send_task_message(inst)
    await AuditService(session).log(actor.id, "task.resend_message",
                                    entity_type="task_instance", entity_id=str(instance_id),
                                    result="ok" if ok else "error")
    return ok


async def recover_scheduler(scheduler, bot, session_factory, actor_id: int) -> dict:
    from bot.services.scheduler_recovery_service import recover_jobs
    counters = await recover_jobs(scheduler, bot, session_factory)
    async with session_factory() as session:
        await AuditService(session).log(actor_id, "scheduler.manual_recover",
                                        new_value=counters)
        await session.commit()
    return counters


async def recalc_next_run(session, actor, config_id: int, scheduler_svc) -> None:
    from bot.services.scheduler_service import compute_next_run
    repo = TaskRepository(session)
    cfg = await repo.get_config(config_id)
    if cfg is None:
        raise ValueError("Шаблон не найден")
    old = cfg.next_run_at
    cfg.next_run_at = compute_next_run(cfg, datetime.utcnow())
    await session.flush()
    await AuditService(session).log(actor.id, "schedule.recalc_next_run",
                                    entity_type="task_config", entity_id=str(config_id),
                                    old_value=str(old), new_value=str(cfg.next_run_at))
    if scheduler_svc is not None:
        # Коммит ДО rebuild — rebuild_config_job открывает СОБСТВЕННУЮ сессию
        # (см. docstring модуля, п.2).
        await session.commit()
        await scheduler_svc.rebuild_config_job(config_id)


async def reregister_reminders(session, actor, instance_id: int, scheduler_svc) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None or inst.status not in OPEN_STATUSES:
        raise ValueError("Напоминания можно перерегистрировать только у открытой задачи")
    if scheduler_svc is not None:
        await scheduler_svc.register_instance_jobs(inst, session=session)
    await AuditService(session).log(actor.id, "reminders.reregister",
                                    entity_type="task_instance", entity_id=str(instance_id))


async def force_close(session, actor, instance_id: int,
                      target_status: str) -> TaskInstance | None:
    if target_status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
        raise ValueError("Допустимо только принудительное завершение или отмена")
    repo = TaskRepository(session)
    inst = await repo.get_instance(instance_id)
    if inst is None:
        return None
    all_non_terminal = [TaskStatus.CREATED, TaskStatus.IN_PROGRESS, TaskStatus.POSTPONED,
                        TaskStatus.WAITING_APPROVAL, TaskStatus.OVERDUE]
    now = datetime.utcnow()
    timestamp_field = "completed_at" if target_status == TaskStatus.COMPLETED else "cancelled_at"
    got = await repo.transition_status(instance_id, all_non_terminal, target_status,
                                       actor.id, f"admin:force_{target_status}",
                                       **{timestamp_field: now})
    await AuditService(session).log(actor.id, f"task.force_{target_status}",
                                    entity_type="task_instance", entity_id=str(instance_id),
                                    result="ok" if got else "no_change")
    return got


async def resend_approval_request(session, bot, actor, instance_id: int) -> None:
    from bot.services.approval_service import ApprovalService
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None or inst.status != TaskStatus.WAITING_APPROVAL:
        raise ValueError("Задача не ожидает подтверждения")
    await ApprovalService(session, bot)._notify_approvers(inst)
    await AuditService(session).log(actor.id, "approval.resend_request",
                                    entity_type="task_instance", entity_id=str(instance_id))


async def manual_auto_approve(session, bot, actor, instance_id: int,
                              scheduler=None) -> TaskInstance | None:
    from bot.services.approval_service import auto_approve_job
    await auto_approve_job(instance_id, bot, lambda: _SameSession(session))
    inst = await TaskRepository(session).get_instance(instance_id)
    await AuditService(session).log(actor.id, "approval.manual_auto_approve",
                                    entity_type="task_instance", entity_id=str(instance_id),
                                    result=inst.status if inst else "unknown")
    return inst if inst and inst.status == TaskStatus.AUTO_APPROVED else None


class _SameSession:
    """Позволяет manual_auto_approve переиспользовать уже открытую сессию
    без создания новой (auto_approve_job принимает session_factory)."""
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


async def recover_pending_deliveries(session, bot, actor) -> int:
    delivery = DeliveryService(session, bot)
    count = 0
    for inst in await TaskRepository(session).get_pending_delivery():
        if await delivery.send_task_message(inst):
            count += 1
    await AuditService(session).log(actor.id, "delivery.recover", new_value=count)
    return count


async def test_topic_send(session, bot, actor, topic_key: str) -> bool:
    from bot.services.topic_service import TopicService
    ok = await TopicService(session, bot).send_test_message(topic_key)
    await AuditService(session).log(actor.id, "topic.test_send", entity_type="topic",
                                    entity_id=topic_key, result="ok" if ok else "error")
    return ok


async def test_private_send(session, bot, actor, user_id: int) -> bool:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise ValueError("Пользователь не найден")
    ok, _ = await DeliveryService(session, bot).send_private(
        user.telegram_id, "🔧 Тестовое сообщение от бота")
    user.private_chat_available = ok
    await AuditService(session).log(actor.id, "user.test_private_send",
                                    entity_type="user", entity_id=str(user_id),
                                    result="ok" if ok else "error")
    return ok


# --------------------------------------------------------------------------
# actor-check
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, PERMISSION):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


# --------------------------------------------------------------------------
# «▶ Ручной запуск»
# --------------------------------------------------------------------------

async def _show_run_list(callback: CallbackQuery, session, page: int) -> None:
    configs = await TaskRepository(session).get_active_configs()
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(configs) // page_size)) if configs else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(c.id, c.title[:50]) for c in configs[start:start + page_size]]
    title = "▶ Ручной запуск — выберите шаблон" if entries else "Активных шаблонов нет"
    await callback.message.edit_text(title, reply_markup=run_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _start_manual_run(callback: CallbackQuery, session, actor, svc: AdminService,
                            config_id: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    bot = callback.bot

    async def op(session) -> None:
        await manual_run_config(session, bot, actor, config_id)

    token = svc.confirm_token(f"task.manual_run.{config_id}", op,
                              required_permission=PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Запустить шаблон «{html_escape(cfg.title)}» прямо сейчас "
        "(создаст новую задачу и отправит её в группу)?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# «📋 Активные задачи»
# --------------------------------------------------------------------------

def _render_instance_row(inst: TaskInstance) -> str:
    due = inst.due_at.strftime("%d.%m %H:%M") if inst.due_at else "—"
    return f"{inst.title_snapshot[:30]} [{inst.status}] до {due}"


async def _show_active_list(callback: CallbackQuery, session, page: int) -> None:
    instances = list(await session.scalars(
        select(TaskInstance).where(TaskInstance.status.in_(ACTIVE_STATUSES))
        .order_by(TaskInstance.due_at)))
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(instances) // page_size)) if instances else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(inst.id, _render_instance_row(inst)) for inst in instances[start:start + page_size]]
    title = "📋 Активные задачи" if entries else "Активных задач нет"
    await callback.message.edit_text(title, reply_markup=active_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_instance_card(callback: CallbackQuery, session, instance_id: int) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    due = inst.due_at.strftime("%d.%m.%Y %H:%M") if inst.due_at else "—"
    lines = [
        f"📋 {html_escape(inst.title_snapshot)}",
        f"Статус: {html_escape(inst.status)}",
        f"Ответственный: {html_escape(inst.responsible_name_snapshot or '—')}",
        f"Срок: {due}",
        f"Попыток доставки: {inst.delivery_attempts}",
    ]
    waiting = inst.status == TaskStatus.WAITING_APPROVAL
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=instance_card_keyboard(inst.id, inst.responsible_user_id, waiting))
    await callback.answer()


async def _start_resend(callback: CallbackQuery, session, actor, svc: AdminService,
                        instance_id: int) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    bot = callback.bot

    async def op(session) -> None:
        await resend_task_message(session, bot, actor, instance_id)

    token = svc.confirm_token(f"task.resend.{instance_id}", op,
                              required_permission=PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Повторно отправить карточку «{html_escape(inst.title_snapshot)}» в группу?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _start_force_close(callback: CallbackQuery, session, actor, svc: AdminService,
                             instance_id: int, target_status: str) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    async def op(session) -> None:
        await force_close(session, actor, instance_id, target_status)

    label = "завершить" if target_status == TaskStatus.COMPLETED else "отменить"
    token = svc.confirm_token(f"task.force_close.{instance_id}.{target_status}", op,
                              required_permission=PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Принудительно {label} задачу «{html_escape(inst.title_snapshot)}»?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _start_resend_approval(callback: CallbackQuery, session, actor, svc: AdminService,
                                 instance_id: int) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None or inst.status != TaskStatus.WAITING_APPROVAL:
        await callback.answer("Задача не ожидает подтверждения", show_alert=True)
        return
    bot = callback.bot

    async def op(session) -> None:
        await resend_approval_request(session, bot, actor, instance_id)

    token = svc.confirm_token(f"approval.resend.{instance_id}", op,
                              required_permission=PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Повторно отправить запрос подтверждения по «{html_escape(inst.title_snapshot)}»?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _start_manual_auto_approve(callback: CallbackQuery, session, actor, svc: AdminService,
                                     instance_id: int) -> None:
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None or inst.status != TaskStatus.WAITING_APPROVAL:
        await callback.answer("Задача не ожидает подтверждения", show_alert=True)
        return
    bot = callback.bot

    async def op(session) -> None:
        await manual_auto_approve(session, bot, actor, instance_id)

    token = svc.confirm_token(f"approval.manual_auto.{instance_id}", op,
                              required_permission=PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Подтвердить «{html_escape(inst.title_snapshot)}» автоматически прямо сейчас "
        "(в обход таймера)?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_operations_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                    actor, svc: AdminService, state=None) -> None:
    """Точка входа для AdminCb.s in ("run", "act", "bak") — все три alias'ятся
    на право tasks.run_manual (SECTION_ALIASES). "bak" пока не реализован в
    этом модуле (Task 35) — попадает в ветку else, тот же UX, что и любой
    ещё не подключённый раздел (main.py:handle_section)."""
    if callback_data.a == "menu":
        allowed = await svc.visible_sections(actor)
        await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
        await callback.answer()
        return
    if callback_data.s == "run":
        await _show_run_list(callback, session, 1)
    elif callback_data.s == "act":
        await _show_active_list(callback, session, 1)
    else:
        await callback.answer("Раздел в разработке")


@router.callback_query(OpsCb.filter())
async def handle_ops_callback(callback: CallbackQuery, callback_data: OpsCb, session,
                              state=None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action == "runlist":
        await _show_run_list(callback, session, callback_data.p)
    elif action == "runpick":
        await _start_manual_run(callback, session, actor, svc, callback_data.id)
    elif action == "actlist":
        await _show_active_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_instance_card(callback, session, callback_data.id)
    elif action == "resend":
        await _start_resend(callback, session, actor, svc, callback_data.id)
    elif action == "close_done":
        await _start_force_close(callback, session, actor, svc, callback_data.id, TaskStatus.COMPLETED)
    elif action == "close_cancel":
        await _start_force_close(callback, session, actor, svc, callback_data.id, TaskStatus.CANCELLED)
    elif action == "resendappr":
        await _start_resend_approval(callback, session, actor, svc, callback_data.id)
    elif action == "autoapprove":
        await _start_manual_auto_approve(callback, session, actor, svc, callback_data.id)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
