"""Отклонение от буквального кода брифа (безопасность, не стиль): брифовый
`subprocess.run(["alembic", "upgrade", "head"], check=True)` НЕ передаёт
`env=`, поэтому alembic (через bot/database/migrations/env.py ->
`get_settings().database_url`) читает `DATABASE_URL` из реального `.env`
проекта — а этот тест затем делает `alembic downgrade 0001`, разрушительную
операцию (откатывает почти всю схему). Запуск буквального брифового кода
против production/dev `.env` мог бы снести реальные данные. Здесь `env=`
ЯВНО передаёт `TEST_DATABASE_URL` (из conftest.py этого пакета) как
`DATABASE_URL` дочернему процессу, гарантируя, что alembic CLI работает
СТРОГО с тестовой БД, независимо от того, что настроено в `.env`.
"""
import os
import subprocess

import pytest

pytestmark = pytest.mark.pg


def _alembic_env() -> dict:
    # Читаем TEST_DATABASE_URL напрямую из окружения (не импортируем из
    # conftest.py этого пакета) — conftest.py делает `pytest.skip(...,
    # allow_module_level=True)`, если переменная не задана, и прямой импорт
    # оттуда распространил бы этот skip на импорт данного модуля непредсказуемым
    # образом. conftest.py уже гарантирует (через фикстуры), что этот файл
    # вообще не выполнится без TEST_DATABASE_URL — здесь читаем то же самое
    # значение независимо, для ясности и надёжности.
    env = dict(os.environ)
    env["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    return env


def test_alembic_upgrade_and_downgrade_roundtrip():
    env = _alembic_env()
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)
    result = subprocess.run(["alembic", "current"], check=True,
                            capture_output=True, text=True, env=env)
    assert "head" in result.stdout or result.stdout.strip() != ""
    subprocess.run(["alembic", "downgrade", "0001"], check=True, env=env)   # seed откатывается
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env)      # снова накатывается
