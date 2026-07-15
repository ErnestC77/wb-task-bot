# Единое напоминание вместо remind1/remind2/overdue + человекочитаемые уведомления о статусе — дизайн

**Дата:** 2026-07-15
**Статус:** утверждён пользователем в диалоге, ожидает записи в implementation-плане

## Контекст и проблема

Два независимых, но обнаруженных в одном диалоге дефекта пользовательского опыта:

1. **Слишком много напоминаний.** Сейчас у каждой задачи может быть до трёх отдельных
   сообщений о её незавершённости: `remind1` (через `TaskConfig.remind_after_hours`),
   `remind2` (через `TaskConfig.second_remind_after_hours`) и переход в статус
   `OVERDUE` через `reminders.overdue_after_hours` (по умолчанию 24ч) с опциональной
   эскалацией owner/partner. Пользователь хочет ровно ОДНО напоминание в чате задачи:
   если через 12 часов после отправки задача всё ещё не взята в работу (статус
   `created`), один раз написать об этом — и всё, больше никаких сообщений.
2. **Нечитаемые статусы в уведомлениях owner.** `status_notification_service.py`
   (`bot/services/status_notification_service.py:65-66`) отправляет владельцу
   технические значения статуса (`created`, `in_progress`) вместо понятного текста
   на русском — хотя такой словарь (`STATUS_LABELS`) уже существует и используется
   в тексте самой задачи (`bot/utils/message_templates.py`).

## Часть А — единое напоминание «не взято в работу»

**Что делаем:** новый одноразовый job `not_taken_reminder_job(instance_id, bot,
session_factory)` в `bot/services/reminder_service.py`, заменяющий собой И
`reminder_job`, И `overdue_job` (обе функции удаляются целиком — после этой замены
у них не останется ни одного вызывающего места).

Логика job'а:
1. Загружает `TaskInstance` по `instance_id`.
2. Если инстанса нет ИЛИ его `status != TaskStatus.CREATED` (задача уже взята в
   работу/выполнена/отменена/etc.) — ничего не делает и выходит.
3. Иначе отправляет ОДНО сообщение в тему чата задачи (`general.group_chat_id` +
   `inst.topic_snapshot`), с упоминанием ответственного через уже существующий
   `mention()`-хелпер (`bot/utils/html_utils.py`, тот же паттерн, что
   `render_task_message` в `message_templates.py`): `f"⏰ Задача {bold(title)} всё ещё
   не взята в работу ({N}ч). Ответственный: {mention(...)}"`, где `{N}` — фактическое
   значение настройки (не захардкожено на 12, чтобы текст соответствовал реальному
   значению, даже если админ его поменяет).
4. Никакого перехода статуса, никакой эскалации owner/partner, никакого счётчика
   отправленных напоминаний — job одноразовый (APScheduler `trigger="date"`),
   поэтому естественным образом не может сработать дважды на один инстанс.
5. Ошибка отправки в Telegram логируется и глотается (тот же паттерн, что у
   удаляемых `reminder_job`/`overdue_job`) — не должна ронять APScheduler.

**Новая настройка:** `reminders.not_taken_after_hours` (int, default `12`,
`min_=1`, `max_=72`) — регистрируется в `SETTINGS_REGISTRY`
(`bot/services/setting_service.py`) в той же категории `"reminders"`, что и
существующие настройки этой группы.

**Регистрация job'а** — заменяет regsitрацию remind1/remind2/overdue в ДВУХ местах:
- `SchedulerService.register_instance_jobs` (`bot/services/scheduler_service.py:140-180`)
  — вызывается при создании каждого нового `TaskInstance`.
- `recover_jobs` (`bot/services/scheduler_recovery_service.py:58-71`) — вызывается
  при рестарте бота для уже существующих открытых инстансов (иначе после рестарта
  инстансы, ожидающие старых remind1/remind2/overdue, потеряли бы своё единственное
  напоминание).

В обоих местах — один `self.scheduler.add_job(not_taken_reminder_job, "date",
run_date=base + timedelta(hours=not_taken_hours), args=[inst.id, bot,
session_factory], id=f"not_taken:{inst.id}", replace_existing=True,
misfire_grace_time=GRACE)` вместо трёх текущих регистраций.

### Явно вне рамок (по решению пользователя в диалоге)

- **Поля БД `TaskConfig.remind_after_hours`, `TaskConfig.second_remind_after_hours`,
  `TaskInstance.remind_after_hours_snapshot`,
  `TaskInstance.second_remind_after_hours_snapshot`, `TaskInstance.reminders_sent`,
  настройки `reminders.overdue_after_hours`, `reminders.overdue_enabled`,
  `reminders.escalation_enabled`, `reminders.escalation_targets`,
  `reminders.max_count`, `reminders.quiet_hours_*`, `reminders.shift_night_to_morning`,
  `reminders.targets`, `reminders.text_template`** — НЕ удаляются и НЕ трогаются.
  Остаются в схеме и в формах редактирования (Telegram `/admin` и веб-админка) как
  неиспользуемые — пользователь явно выбрал не делать миграцию и не трогать UI
  редактирования ради минимизации риска. Значения этих полей просто перестают
  на что-либо влиять.
- Функции `reminder_job`/`overdue_job` удаляются как код (не просто перестают
  вызываться) — в отличие от полей БД, это внутренний Python-код без
  внешних зависимостей (UI/схема), поэтому оставлять его мёртвым не нужно.

## Часть Б — человекочитаемые статусы в уведомлениях владельцу

**Что делаем:** `status_notification_service.py:65-66` заменяет прямую вставку
`log.old_status`/`log.new_status` на `STATUS_LABELS.get(status, status)` —
переиспользуется существующий словарь из `bot/utils/message_templates.py`
(`STATUS_LABELS`, уже содержит эмодзи + русский текст для всех статусов
`TaskStatus`, включая `created`/`in_progress`/`completed`/`overdue`/etc.).

Пример: было `"Задача X: created → in_progress — Валя"`, станет `"Задача X: 🆕
Создана → 🔄 В работе — Валя"`.

`STATUS_LABELS` импортируется из `message_templates.py` (не дублируется).

## Тестирование

- `not_taken_reminder_job`: если статус `CREATED` — сообщение отправлено ровно
  один раз, текст содержит текущее значение `reminders.not_taken_after_hours`
  (не захардкоженное 12); если статус уже `IN_PROGRESS`/`COMPLETED`/etc. —
  `bot.send_message` не вызывается вовсе.
- `register_instance_jobs`: регистрирует ровно один job с id `not_taken:{id}`,
  время срабатывания = `scheduled_at + reminders.not_taken_after_hours` часов
  (проверить с нестандартным значением настройки, не только дефолтным 12);
  НЕ регистрирует `remind1:`/`remind2:`/`overdue:` job'ы.
- `recover_jobs`: то же самое для восстановления при рестарте — один
  `not_taken:{id}` на каждый открытый инстанс вместо прежних трёх.
- `reminder_job`/`overdue_job` и их прежние тесты удаляются вместе с самими
  функциями (не остаются как тесты мёртвого кода).
- `status_notification_service`: уведомление содержит `STATUS_LABELS`-текст
  (например «🔄 В работе»), а не сырое значение `new_status`/`old_status`,
  для каждого статуса, который реально появляется в `status_notifications.statuses`.

## Критерии готовности

1. Задача, не взятая в работу спустя `reminders.not_taken_after_hours` часов
   (по умолчанию 12), получает ровно одно сообщение в чат с упоминанием
   ответственного — и больше никаких напоминаний/эскалаций по ней не приходит.
2. Задача, взятая в работу до истечения этого времени, вообще не получает
   сообщения.
3. После рестарта бота уже существующие открытые задачи по-прежнему получат это
   единственное напоминание в правильное время (через recover_jobs), не дважды
   и не с прежним remind1/remind2/overdue поведением.
4. Уведомления owner о смене статуса задачи показывают понятный русский текст
   («Создана», «В работе» и т.д.), а не технические значения enum.
5. Поля БД и формы редактирования старого механизма (remind_after_hours и т.д.)
   не изменены и не удалены — только логика, которая их читала, отключена.
