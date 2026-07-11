"""Клавиатуры для сообщений задач.

Кнопки зависят от статуса задачи и сценария (article_check / simple).
Старая механика с отдельной кнопкой статуса «требуется внимание» (статус
всей задачи) сюда не входит — она убрана из плана целиком (Task 20).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import TaskInstance, TaskScenario, TaskStatus


class TaskCb(CallbackData, prefix="t"):
    a: str   # start|postpone|question|finish|done
    i: int   # instance_id


def _btn(text: str, action: str, instance_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=TaskCb(a=action, i=instance_id).pack())


def keyboard_for_status(inst: TaskInstance) -> InlineKeyboardMarkup | None:
    check = inst.scenario_snapshot == TaskScenario.ARTICLE_CHECK
    rows: list[list[InlineKeyboardButton]] = []
    if inst.status in (TaskStatus.CREATED, TaskStatus.POSTPONED):
        rows = [
            [_btn("🔄 Начать проверку" if check else "🔄 В работе", "start", inst.id)],
            [_btn("⏳ Перенести на завтра", "postpone", inst.id)],
            [_btn("❓ Есть вопрос", "question", inst.id)],
        ]
    elif inst.status == TaskStatus.IN_PROGRESS:
        if check:
            rows = [
                [_btn("▶ Продолжить проверку", "start", inst.id)],
                [_btn("❓ Есть вопрос", "question", inst.id)],
                [_btn("✅ Завершить проверку", "finish", inst.id)],
            ]
        else:
            rows = [
                [_btn("✅ Выполнено", "done", inst.id)],
                [_btn("❓ Есть вопрос", "question", inst.id)],
            ]
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)
