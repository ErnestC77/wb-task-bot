"""Шаблоны текста сообщений задач.

Global Constraint: все динамические значения (названия задач, имена
пользователей, тексты вопросов, названия товаров и т.п.) ОБЯЗАТЕЛЬНО
экранируются через html_escape перед вставкой в HTML-сообщение.
"""
from bot.database.models import TaskInstance, TaskQuestion, TaskStatus
from bot.utils.html_utils import bold, html_escape, mention

STATUS_LABELS = {
    TaskStatus.CREATED: "🆕 Создана",
    TaskStatus.IN_PROGRESS: "🔄 В работе",
    TaskStatus.COMPLETED: "✅ Выполнена",
    TaskStatus.WAITING_APPROVAL: "⏳ Ждет подтверждения",
    TaskStatus.APPROVED: "✅ Подтверждена",
    TaskStatus.AUTO_APPROVED: "✅ Авто-подтверждена",
    TaskStatus.POSTPONED: "⏳ Перенесена",
    TaskStatus.PROBLEM: "⚠ Проблема",
    TaskStatus.OVERDUE: "🔥 Просрочена",
    TaskStatus.CANCELLED: "❌ Отменена",
}


def _due_at_label(inst: TaskInstance) -> str:
    """due_at не всегда совпадает по дате с scheduled_at: due_days_offset>0
    или срок "сутки" (due_time не задан в конфиге -> due_at = scheduled_at
    + 24ч, calendar.date() всегда на день позже) — жёсткое "сегодня" тут
    вводило в заблуждение."""
    days = (inst.due_at.date() - inst.scheduled_at.date()).days
    if days == 0:
        return f"сегодня до {inst.due_at.strftime('%H:%M')}"
    if days == 1:
        return f"завтра до {inst.due_at.strftime('%H:%M')}"
    return f"до {inst.due_at.strftime('%d.%m %H:%M')}"


def render_task_message(inst: TaskInstance) -> str:
    lines = [f"📌 {bold(inst.title_snapshot)}"]
    if inst.description_snapshot:
        lines.append(html_escape(inst.description_snapshot))
    if inst.responsible_telegram_id_snapshot is not None:
        lines.append("Ответственный: "
                     f"{mention(inst.responsible_telegram_id_snapshot, inst.responsible_name_snapshot)}")
    elif inst.responsible_name_snapshot:
        lines.append(f"Ответственный: {html_escape(inst.responsible_name_snapshot)}")
    if inst.due_at:
        lines.append(f"Срок: {_due_at_label(inst)}")
    lines.append(f"Статус: {STATUS_LABELS.get(inst.status, html_escape(inst.status))}")
    return "\n".join(lines)


def render_reminder(inst: TaskInstance, template: str) -> str:
    return html_escape(template).replace("{title}", html_escape(inst.title_snapshot))


def render_approval_request(inst: TaskInstance) -> str:
    return (f"🔔 Задача выполнена и ждет подтверждения\n"
            f"{bold(inst.title_snapshot)}\n"
            f"Исполнитель: {html_escape(inst.responsible_name_snapshot)}\n"
            f"Авто-подтверждение через {inst.approval_timeout_hours_snapshot} ч.")


def render_question_message(q: TaskQuestion, inst: TaskInstance,
                            item=None) -> str:
    head = "❓ Вопрос по артикулу" if item is not None else "❓ Вопрос по задаче"
    lines = [head, "",
             f"Сотрудник: {html_escape(inst.responsible_name_snapshot)}",
             f"Задача: {html_escape(inst.title_snapshot)}"]
    if item is not None:
        lines.append(f"Артикул: {html_escape(item.article_snapshot)}")
        if item.product_name_snapshot:
            lines.append(f"Товар: {html_escape(item.product_name_snapshot)}")
    lines.append(f"Вопрос: {html_escape(q.question_text)}")
    lines.append(f"Время: {q.created_at.strftime('%d.%m.%Y %H:%M') if q.created_at else ''}")
    return "\n".join(lines)
