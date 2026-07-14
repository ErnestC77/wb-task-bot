"""Интеграционный тест «Журнала отправок» на реальном PostgreSQL: в журнал
попадают только SENT, повторный прогон не создаёт дублей (идемпотентность
через sheet_logged_at)."""
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.database.models import DeliveryStatus, Role, TaskInstance
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.google_sheets_service import delivery_log_job
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_delivery_log_job_only_sent_and_idempotent_pg(pg_session_factory):
    async with pg_session_factory() as s:
        topic = await TopicRepository(s).upsert(topic_key="goods", topic_name="Товары")
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="log_me_pg", title="Задача", scenario="simple",
            schedule_type="daily", topic_id=topic.id, due_time=time(12, 0),
            responsible_user_id=user.id, is_active=True))
        svc = TaskService(s)
        sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
        sent.delivery_status = DeliveryStatus.SENT
        sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
        sent_id, pending_id = sent.id, pending.id

    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")
    fake_client = MagicMock()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), pg_session_factory)
        await delivery_log_job(AsyncMock(), pg_session_factory)   # повторный прогон

    fake_client.append_rows.assert_called_once()
    _sheet, rows, _header = fake_client.append_rows.call_args.args
    assert len(rows) == 1
    async with pg_session_factory() as s:
        assert (await s.get(TaskInstance, sent_id)).sheet_logged_at is not None
        assert (await s.get(TaskInstance, pending_id)).sheet_logged_at is None
