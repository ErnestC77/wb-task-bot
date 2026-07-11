from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import CheckStatus
from bot.utils.html_utils import html_escape

STATUS_ICONS = {
    CheckStatus.PENDING: "▫",
    CheckStatus.CHECKED_NO_ACTION: "✅",
    CheckStatus.ACTION_REQUIRED: "⚠",
    CheckStatus.QUESTION: "❓",
}


class ChkCb(CallbackData, prefix="c"):
    a: str          # open|mark|nav|fin|cancel|gq
    s: int          # session_id
    it: int = 0     # item_id
    v: int = 0      # version
    st: str = ""    # ok|act|q
    b: int = 0      # batch


def render_batch(view, show_names: bool) -> str:
    lines = ["📦 <b>Проверка артикулов</b>", "",
             f"Пачка {view.batch} из {view.total_batches}",
             f"Проверено: {view.checked} из {view.total}", ""]
    start = (view.batch - 1) * view.session.batch_size if hasattr(view.session, "batch_size") else 0
    for n, item in enumerate(view.items, start=start + 1):
        icon = STATUS_ICONS.get(item.check_status, "▫")
        name = (f" — {html_escape(item.product_name_snapshot)}"
                if show_names and item.product_name_snapshot else "")
        lines.append(f"{icon} {n}. {html_escape(item.article_snapshot)}{name}")
    return "\n".join(lines)


def batch_keyboard(view, allow_prev: bool) -> InlineKeyboardMarkup:
    rows = []
    for item in view.items:
        icon = STATUS_ICONS.get(item.check_status, "▫")
        rows.append([InlineKeyboardButton(
            text=f"{icon} {item.article_snapshot}",
            callback_data=ChkCb(a="open", s=view.session.id, it=item.id).pack())])
    nav = []
    if view.batch > 1 and allow_prev:
        nav.append(InlineKeyboardButton(
            text="⬅ Пред. пачка",
            callback_data=ChkCb(a="nav", s=view.session.id, b=view.batch - 1).pack()))
    if view.batch < view.total_batches:
        nav.append(InlineKeyboardButton(
            text="След. пачка ➡",
            callback_data=ChkCb(a="nav", s=view.session.id, b=view.batch + 1).pack()))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        text="✅ Завершить пачку",
        callback_data=ChkCb(a="fin", s=view.session.id, b=view.batch).pack())])
    rows.append([
        InlineKeyboardButton(text="❓ Общий вопрос",
                             callback_data=ChkCb(a="gq", s=view.session.id).pack()),
        InlineKeyboardButton(text="✖ Отменить проверку",
                             callback_data=ChkCb(a="cancel", s=view.session.id).pack()),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def item_status_keyboard(item, session_id: int) -> InlineKeyboardMarkup:
    def cb(st: str) -> str:
        return ChkCb(a="mark", s=session_id, it=item.id, v=item.version, st=st).pack()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Действий не требуется", callback_data=cb("ok"))],
        [InlineKeyboardButton(text="⚠ Требуются действия", callback_data=cb("act"))],
        [InlineKeyboardButton(text="❓ Есть вопрос", callback_data=cb("q"))],
        [InlineKeyboardButton(text="⬅ К пачке",
                              callback_data=ChkCb(a="nav", s=session_id, b=0).pack())],
    ])
