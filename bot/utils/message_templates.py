"""Шаблоны текста сообщений задач.

Заглушка до Task 11 — полная реализация (текст по статусу/сценарию) будет там.
"""
from bot.database.models import TaskInstance


def render_task_message(inst: TaskInstance) -> str:
    return inst.title_snapshot
