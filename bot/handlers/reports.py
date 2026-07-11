"""/report — ручное формирование отчёта.

Security note (тот же mandate, что и в bot/handlers/tasks.py): actor
проверяется на None ДО обращения к его полям. Доступ не ограничен ролью —
/report доступен любому зарегистрированному сотруднику, но объём отчёта
зависит от роли: owner/partner получают полный отчёт по всем (как в
еженедельной рассылке), остальные — только свой (собственная статистика,
свои проблемные артикулы/просроченные проверки/вопросы), см. брифовое
уточнение «сотрудник — свой отчёт; owner/partner — полный».
"""
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.database.models import Role, User
from bot.services.report_service import ReportService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService

router = Router(name=__name__)


async def _actor_or_deny(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await message.answer("Недостаточно прав: вы не зарегистрированы в системе.")
    return actor


@router.message(Command("report"))
async def cmd_report(message: Message, session) -> None:
    actor = await _actor_or_deny(message, session)
    if actor is None:
        return
    settings = SettingService(session)
    svc = ReportService(session)
    period_days = int(await settings.get("reports.period_days"))
    end = datetime.utcnow()
    start = end - timedelta(days=period_days)

    is_owner_or_partner = actor.role in (Role.OWNER, Role.PARTNER)
    if is_owner_or_partner:
        data = await svc.collect_metrics(start, end)
        fmt = str(await settings.get("reports.format"))
    else:
        data = await svc.collect_metrics(start, end, employee_id=actor.id)
        fmt = "full"          # свой отчёт — детальный, чтобы сотрудник видел свои проблемы

    text = await svc.render(data, fmt)
    await message.answer(text)
