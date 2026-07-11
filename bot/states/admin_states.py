from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """FSM-инфраструктура для админ-панели.

    waiting_value/waiting_confirm — универсальные состояния каркаса (Task 23).
    Специализированные состояния разделов добавляются в Tasks 24-27.
    """

    waiting_value = State()
    waiting_confirm = State()

    # Task 25 — раздел «Пользователи и роли»: FSM добавления (add_id -> add_name
    # -> add_role, где add_role выбирается кнопкой, а не сообщением) и
    # редактирования имени/username существующего пользователя.
    waiting_add_id = State()
    waiting_add_name = State()
    waiting_add_role = State()
    waiting_edit_name = State()
    waiting_edit_username = State()

    # Task 26 — раздел «🗂 Темы Telegram»: редактирование названия,
    # message_thread_id (после сохранения — автоматическая тестовая отправка)
    # и csv-строки типов событий существующей темы.
    waiting_topic_name = State()
    waiting_topic_thread = State()
    waiting_topic_events = State()

    # Task 27 — раздел «✅ Шаблоны задач»: единое состояние на весь мастер
    # создания шаблона (шаг хранится в FSM data как "step", т.к. шаги вперемешку
    # текстовые и кнопочные — см. bot/handlers/admin/task_configs.py) и единое
    # состояние на редактирование ОДНОГО текстового поля существующего шаблона
    # (whitelist EDITABLE_FIELDS, поле хранится в data как "field").
    waiting_cfg_create = State()
    waiting_cfg_edit = State()
