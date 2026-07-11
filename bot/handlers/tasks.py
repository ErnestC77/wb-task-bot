"""/today, /my_tasks, /overdue.

Security note (Task 20 mandate): каждый handler получает actor через
UserService.get_actor и отказывает при actor is None ДО любого обращения к
TaskRepository. Видимость списков: owner/partner видят задачи ВСЕХ
сотрудников (правило плана), остальные роли — только свои (фильтр по
responsible_user_id == actor.id уже на стороне handler'а, репозиторий отдаёт
«сырые» списки без авторизации).
"""
from datetime import date

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.database.models import Role, TaskInstance, TaskStatus, User
from bot.database.repositories.task_repository import TaskRepository
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.utils.datetime_utils import now_tz
from bot.utils.html_utils import html_escape
from bot.utils.message_templates import STATUS_LABELS
from bot.utils.pagination import paginate

router = Router(name=__name__)


def _sees_all(actor: User) -> bool:
    """owner и partner видят все задачи (правило плана), остальные — только свои."""
    return actor.role in (Role.OWNER, Role.PARTNER)


def _render_list(header: str, items: list[TaskInstance], page_size: int) -> str:
    if not items:
        return f"{header}\n\nНет задач."
    page_items, total_pages = paginate(items, 1, page_size)
    lines = [header, ""]
    for inst in page_items:
        status = STATUS_LABELS.get(inst.status, html_escape(inst.status))
        due = f", срок до {inst.due_at.strftime('%d.%m %H:%M')}" if inst.due_at else ""
        resp = (f" ({html_escape(inst.responsible_name_snapshot)})"
                if inst.responsible_name_snapshot else "")
        lines.append(f"#{inst.id} {html_escape(inst.title_snapshot)}{resp} — {status}{due}")
    if total_pages > 1:
        lines.append("")
        lines.append(f"Показано {len(page_items)} из {len(items)} (стр. 1/{total_pages})")
    return "\n".join(lines)


async def _actor_or_deny(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await message.answer("Недостаточно прав: вы не зарегистрированы в системе.")
    return actor


@router.message(Command("today"))
async def cmd_today(message: Message, session) -> None:
    actor = await _actor_or_deny(message, session)
    if actor is None:
        return
    repo = TaskRepository(session)
    settings = SettingService(session)
    tz_name = str(await settings.get("general.timezone"))
    today_local: date = now_tz(tz_name).date()
    items = await repo.get_by_scheduled_date(today_local)
    if not _sees_all(actor):
        items = [i for i in items if i.responsible_user_id == actor.id]
    page_size = int(await settings.get("general.page_size"))
    await message.answer(_render_list("📅 Задачи на сегодня", items, page_size))


@router.message(Command("my_tasks"))
async def cmd_my_tasks(message: Message, session) -> None:
    actor = await _actor_or_deny(message, session)
    if actor is None:
        return
    repo = TaskRepository(session)
    sees_all = _sees_all(actor)
    items = (await repo.get_all_open_instances() if sees_all
             else await repo.get_open_instances_for_user(actor.id))
    header = "📝 Открытые задачи (все сотрудники)" if sees_all else "📝 Мои задачи"
    page_size = int(await SettingService(session).get("general.page_size"))
    await message.answer(_render_list(header, items, page_size))


@router.message(Command("overdue"))
async def cmd_overdue(message: Message, session) -> None:
    actor = await _actor_or_deny(message, session)
    if actor is None:
        return
    repo = TaskRepository(session)
    items = await repo.get_by_status(TaskStatus.OVERDUE)
    if not _sees_all(actor):
        items = [i for i in items if i.responsible_user_id == actor.id]
    page_size = int(await SettingService(session).get("general.page_size"))
    await message.answer(_render_list("🔥 Просроченные задачи", items, page_size))
