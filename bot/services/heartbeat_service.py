"""Heartbeat для healthcheck bot-контейнера.

Инцидент 2026-07-17: healthcheck bot-сервиса в docker-compose.yml проверял
только `socket.create_connection(('db', 5432))` — т.е. "доступен ли порт
Postgres с точки зрения контейнера", а не "жив ли сам процесс/event loop".
Зависший (но не упавший) dispatcher/scheduler мог бы держать контейнер
"healthy" сколь угодно долго. heartbeat_job пишет текущее unix-время в файл
ПОСЛЕ реального запроса к БД через тот же event loop, где крутится
polling/scheduler — если job перестал выполняться (event loop встал) или БД
недоступна, файл перестаёт обновляться, и healthcheck (docker-compose.yml)
считает контейнер unhealthy по возрасту файла.
"""
import time

from sqlalchemy import text

from bot.utils.logger import get_logger

logger = get_logger(__name__)

HEARTBEAT_PATH = "/tmp/wb_bot_heartbeat"


async def heartbeat_job(session_factory, path: str = HEARTBEAT_PATH) -> None:
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
    with open(path, "w") as f:
        f.write(str(time.time()))
