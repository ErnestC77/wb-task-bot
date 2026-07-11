from unittest.mock import AsyncMock

import pytest

from bot.database.models import Article, Role
from bot.database.repositories.article_repository import ArticleRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


class FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data = {}
        self.state = None


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно)
# ---------------------------------------------------------------------------

async def test_manual_add_respects_setting(session):
    from bot.handlers.admin.articles import add_manual_article
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    with pytest.raises(PermissionError):
        await add_manual_article(session, owner, "77777777", "Ручной товар")
    await SettingService(session).set("article_check.allow_manual_article_add", True, owner.id)
    art = await add_manual_article(session, owner, "77777777", "Ручной товар")
    await session.commit()
    assert art.source == "manual" and art.is_active is True


async def test_duplicate_article_rejected(session):
    from bot.handlers.admin.articles import add_manual_article
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await SettingService(session).set("article_check.allow_manual_article_add", True, owner.id)
    await add_manual_article(session, owner, "77777777", "Товар")
    await session.commit()
    with pytest.raises(ValueError):
        await add_manual_article(session, owner, "77777777", "Дубликат")


# ---------------------------------------------------------------------------
# Whitelist-редактирование
# ---------------------------------------------------------------------------

async def test_whitelist_field_editing(session):
    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="12345678", product_name="Товар")
    await session.commit()
    from bot.handlers.admin.articles import EDITABLE_FIELDS, apply_field_edit
    assert "product_name" in EDITABLE_FIELDS and "article" not in EDITABLE_FIELDS
    ok, _ = await apply_field_edit(session, owner, art.id, "product_name", "Новое имя")
    assert ok is True and art.product_name == "Новое имя"
    ok, msg = await apply_field_edit(session, owner, art.id, "article", "hack")
    assert ok is False and "запрещ" in msg.lower()


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_art_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_art_callback(callback, ArtCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_art_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_art_callback(callback, ArtCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Список / фильтр архивных
# ---------------------------------------------------------------------------

def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


async def test_list_hides_inactive_by_default(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    active = await ArticleRepository(session).upsert(article="11111111", is_active=True)
    inactive = await ArticleRepository(session).upsert(article="22222222", is_active=False)
    await session.commit()
    assert bool(await SettingService(session).get("article_check.include_archived")) is False

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="list"), session)
    kb = _reply_markup(callback)
    ids = [ArtCb.unpack(row[0].callback_data).id for row in kb.inline_keyboard
          if row and row[0].callback_data.startswith("ar:card")]
    assert active.id in ids
    assert inactive.id not in ids


async def test_list_shows_inactive_when_include_archived_enabled(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    await SettingService(session).set("article_check.include_archived", True, owner.id)
    inactive = await ArticleRepository(session).upsert(article="22222222", is_active=False)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="list"), session)
    kb = _reply_markup(callback)
    ids = [ArtCb.unpack(row[0].callback_data).id for row in kb.inline_keyboard
          if row and row[0].callback_data.startswith("ar:card")]
    assert inactive.id in ids


async def test_add_button_hidden_when_setting_disabled(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    await session.commit()
    assert bool(await SettingService(session).get("article_check.allow_manual_article_add")) is False

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="list"), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "➕ Добавить вручную" not in labels


async def test_add_button_shown_when_setting_enabled(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    await SettingService(session).set("article_check.allow_manual_article_add", True, owner.id)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="list"), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "➕ Добавить вручную" in labels


async def test_direct_add_callback_rejected_when_setting_disabled(session):
    """Кнопка скрыта в UI, но прямой (например, подделанный) callback всё
    равно обязан быть безопасным — брифовое требование."""
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="add"), session, state)
    callback.answer.assert_awaited_with("Ручное добавление отключено настройкой", show_alert=True)
    assert state.state is None


# ---------------------------------------------------------------------------
# Карточка / редактирование полей
# ---------------------------------------------------------------------------

async def test_show_card_renders_fields(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="33333333", product_name="Товар",
                                                   sort_order=5)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="card", id=art.id), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "33333333" in text and "Товар" in text and "5" in text


async def test_show_card_unknown_article_rejected(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="card", id=999999), session)
    callback.answer.assert_awaited_with("Артикул не найден", show_alert=True)


async def test_edit_sort_order_via_fsm_message(session):
    from bot.handlers.admin.articles import handle_art_callback, handle_art_edit_message
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="44444444", sort_order=0)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="editorder", id=art.id), session, state)
    assert state.state is not None and state.data["field"] == "sort_order"

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "9"
    await handle_art_edit_message(message, session, state)
    assert art.sort_order == 9
    assert state.state is None


async def test_edit_responsible_requires_active_user(session):
    from bot.handlers.admin.articles import handle_art_callback, handle_art_edit_message
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="55555555")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="editresp", id=art.id), session, state)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "999999"                        # несуществующий user_id
    await handle_art_edit_message(message, session, state)
    assert art.responsible_user_id is None
    assert state.state is not None                 # состояние НЕ сброшено — ошибка ввода


async def test_cancel_button_clears_fsm_state(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="66666666", sort_order=1)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="editorder", id=art.id), session, state)
    assert state.state == AdminStates.waiting_art_edit

    await handle_art_callback(callback, ArtCb(a="card", id=art.id), session, state)
    assert state.state is None and state.data == {}
    assert art.sort_order == 1


# ---------------------------------------------------------------------------
# Активация / деактивация через confirm_token
# ---------------------------------------------------------------------------

async def test_deactivate_goes_through_confirm_token_not_immediate(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="77777779", is_active=True)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="toggle", id=art.id), session)
    assert art.is_active is True                   # ещё не деактивирован — ждёт подтверждения
    kb = _reply_markup(callback)
    assert "Подтвердить" in kb.inline_keyboard[0][0].text or "✅" in kb.inline_keyboard[0][0].text


async def test_reactivate_is_immediate_no_confirm_needed(session):
    from bot.handlers.admin.articles import handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    art = await ArticleRepository(session).upsert(article="88888888", is_active=False)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="toggle", id=art.id), session)
    assert art.is_active is True


# ---------------------------------------------------------------------------
# Мастер ручного добавления
# ---------------------------------------------------------------------------

async def test_manual_add_wizard_full_flow(session):
    from bot.handlers.admin.articles import handle_art_add_message, handle_art_callback
    from bot.keyboards.admin.articles import ArtCb

    owner = await _owner(session)
    await SettingService(session).set("article_check.allow_manual_article_add", True, owner.id)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_art_callback(callback, ArtCb(a="add"), session, state)
    assert state.state is not None

    m1 = AsyncMock()
    m1.from_user.id = owner.telegram_id
    m1.text = "99999999"
    await handle_art_add_message(m1, session, state)
    assert state.data["step"] == "product_name"

    m2 = AsyncMock()
    m2.from_user.id = owner.telegram_id
    m2.text = "Новый товар"
    await handle_art_add_message(m2, session, state)
    assert state.state is None

    created = await ArticleRepository(session).get_by_article("99999999")
    assert created is not None and created.product_name == "Новый товар" and created.source == "manual"


# ---------------------------------------------------------------------------
# Regression (Tasks 24-27 lesson): каждая клавиатура — реальный .pack()/
# .unpack() round-trip. Ни одно поле ArtCb не содержит ":".
# ---------------------------------------------------------------------------

def test_artcb_default_fields_roundtrip():
    from bot.keyboards.admin.articles import ArtCb

    cb = ArtCb(a="noop")
    packed = cb.pack()
    unpacked = ArtCb.unpack(packed)
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.p == 1


def test_articles_list_keyboard_pack_unpack_roundtrip_with_and_without_add_button():
    from bot.keyboards.admin.articles import ArtCb, articles_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(i, f"art{i}") for i in range(1, 4)]
    kb = articles_list_keyboard(entries, page=2, total_pages=3, allow_add=True)
    for row in kb.inline_keyboard[:3]:
        assert ArtCb.unpack(row[0].callback_data).a == "card"
    add_row = kb.inline_keyboard[3]
    assert ArtCb.unpack(add_row[0].callback_data).a == "add"
    nav = kb.inline_keyboard[4]
    assert ArtCb.unpack(nav[0].callback_data).p == 1
    assert ArtCb.unpack(nav[2].callback_data).p == 3
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "art" and back.a == "menu"

    kb_no_add = articles_list_keyboard(entries, page=1, total_pages=1, allow_add=False)
    labels = [btn.text for row in kb_no_add.inline_keyboard for btn in row]
    assert "➕ Добавить вручную" not in labels


def test_article_card_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.articles import ArtCb, article_card_keyboard

    kb = article_card_keyboard(article_id=987654321, is_active=True)
    for row in kb.inline_keyboard[:4]:
        cb = ArtCb.unpack(row[0].callback_data)
        assert cb.id == 987654321
    assert ArtCb.unpack(kb.inline_keyboard[3][0].callback_data).a == "toggle"
    assert kb.inline_keyboard[3][0].text.startswith("🚫")


def test_cancel_edit_and_cancel_add_keyboards_pack_unpack_roundtrip():
    from bot.keyboards.admin.articles import ArtCb, cancel_add_keyboard, cancel_edit_keyboard

    edit_cb = ArtCb.unpack(cancel_edit_keyboard(article_id=42).inline_keyboard[0][0].callback_data)
    assert edit_cb.a == "card" and edit_cb.id == 42

    add_cb = ArtCb.unpack(cancel_add_keyboard().inline_keyboard[0][0].callback_data)
    assert add_cb.a == "list" and add_cb.p == 1
