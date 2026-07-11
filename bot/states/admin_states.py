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

    # Task 28 — раздел «📅 Расписания»: редактирование одного текстового поля
    # расписания существующего шаблона (whitelist SCHEDULE_FIELD_LIST, поле
    # хранится в data как "field", как и в waiting_cfg_edit Task 27).
    waiting_sch_edit = State()

    # Task 29 — раздел «📦 Артикулы»: редактирование одного текстового поля
    # существующего артикула (название/sort_order/ответственный — поле хранится
    # в data как "field") и мастер ручного добавления (шаг в data как "step":
    # "article" -> "product_name").
    waiting_art_edit = State()
    waiting_art_add = State()

    # Task 30 — раздел «⚠ Категории проблем»/«🛠 Варианты решений»/«Категории
    # товаров»: переименование существующей записи (текст) и мастер добавления
    # (шаг в data как "step": "name" -> "require_comment" (кнопка) ->
    # "default_next_check_days" (текст) — два последних шага пропускаются для
    # kind="article_categories", у которого нет этих полей).
    waiting_dic_rename = State()
    waiting_dic_add = State()
