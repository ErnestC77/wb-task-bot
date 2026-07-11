"""Task 36: недостающий пункт регрессионной матрицы раздела 15 (пакетная
проверка артикулов) — №12: открытый вопрос обрабатывается по настройке
article_check.allow_finish_with_open_questions."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.models import CheckStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService
from tests.test_article_check_service import seed


async def test_finish_blocked_by_open_question_when_setting_false(session):   # тест 12
    inst, valya, _ = await seed(session, n_articles=2)
    await SettingService(session).set(
        "article_check.allow_finish_with_open_questions", False, valya.id)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    items = await chk.get_items_for_batch(s.id, 1, 15)
    await svc.mark(items[0].id, 1, CheckStatus.QUESTION, valya)
    await svc.mark(items[1].id, 1, CheckStatus.CHECKED_NO_ACTION, valya)

    from bot.services.question_service import QuestionService
    receiver = await UserRepository(session).upsert(telegram_id=99, name="R",
                                                     role=Role.OWNER)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                     chat=SimpleNamespace(id=99))
    await QuestionService(session, bot).ask(inst, valya, "?", item=items[0])

    ok, reason = await svc.finish_check(inst, valya)
    assert ok is False and "вопрос" in reason.lower()

    await SettingService(session).set(
        "article_check.allow_finish_with_open_questions", True, valya.id)
    ok, _ = await svc.finish_check(inst, valya)
    assert ok is True                                    # теперь разрешено с открытым вопросом
