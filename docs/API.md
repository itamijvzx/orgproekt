# API OrgProekt — контракты и локальная проверка

## Управление доступом — этап 4

База `/api/v1/access`. Чтение требует MFA-сессии; административные команды и получение локального кода — fresh reauth (10 минут), Origin/CSRF. Команды с телом используют `idempotency-key` (8–80 символов), действия над существующим объектом — `expected_version` и `note` (5–240 символов). Точное повторение возвращает результат без нового эффекта; другое тело под тем же ключом/устаревшая версия — 409. Авторизация проверяется на сервере, в том числе после ожидания блокировки. Все изменения и выдача кода журналируются транзакционно.

| Метод и путь | Контракт |
|---|---|
| GET `/options` | Технические возможности и разрешённые отделы администратора/manager; прочим 404 |
| GET `/users`, `/users/{id}` | Только access_admin; поиск `q`, `active`, пагинация; деталь содержит назначения, сроки, основания и состояния, без секретов |
| POST `/invitations` | `username`, `display_name`, `basis`, `expires_hours=1..72` (24 по умолчанию); логин ASCII 3–80, нормализуется в lowercase; существующий/действующее приглашение — 409 |
| GET `/invitations` | access_admin; метаданные/состояние/версия, без кода |
| POST `/invitations/{id}/delivery` | Без тела/ключа повтора; access_admin + reauth; только development/test; каждый показ журналируется, повтор возможен пока приглашение действует |
| POST `/invitations/{id}/cancel` | Версия/основание, очищает локальную копию кода |
| POST `/requests` | `user_id`, `role`, `department_id`, `valid_from`, `valid_until`, `basis`; access_admin, кроме себя; роль auditor/hr/department/reviewer/manager требует отдел, access_admin — null |
| GET `/requests` | access_admin или scoped manager, `state=pending/approved/rejected/cancelled`; фильтрация прав до вычисления total и выдачи страницы |
| POST `/requests/{id}/decision` | Независимый manager требуемой области; `outcome=approve/reject`, `checked`, версия/основание. Для approve checked=true; создаёт роль и завершает сессии получателя |
| POST `/requests/{id}/cancel` | access_admin, ожидающий запрос, версия/основание |
| POST `/users/{id}/block`, `/unblock`, `/revoke-sessions` | access_admin для другого пользователя, версия/основание; пароль/MFA не изменяются |
| POST `/grants/{id}/revoke` | access_admin для чужого назначения, версия **пользователя**, основание; immutable отзыв и завершение сессий |
| GET `/events` | access_admin, минимальная security-история, без токенов |

Списки возвращают `items/total/offset/limit`; offset 0–10000, limit 1–100 (users/invitations/requests 20, events 25 по умолчанию). Новый период timezone-aware, с обязательным концом не далее 366 дней; пересечение одинаковой роли/области — 409, неверный период/смешение технических и бизнес-ролей — 422. Manager не получает каталог пользователей/приглашения. Новое назначение access_admin подтверждает manager всех текущих отделов. Запрос, автор или получатель которого совпадает с подтверждающим, не утверждается.

Публичный POST `/api/v1/auth/accept-invitation`: `token` и собственный `password` 14–200 символов, пробелы значимы. Проверяет same-origin и лимит попыток, срок/отмену/использование/полномочия пригласившего; ошибка нейтральная 400. Успех `{ok:true}` создаёт аккаунт без ролей, не создаёт сессию. Далее обычный вход и MFA. Токен не передаётся в URL. Типизированные контракты: `frontend/openapi.json`, `schema.generated.ts`. Сценарий: [ACCESS_WORKFLOW.md](ACCESS_WORKFLOW.md).

## Отчёты по предложениям — этап 4

База `/api/v1/reports`. Все маршруты требуют действующую серверную MFA-сессию и текущие права на полную область отчёта. POST дополнительно проверяет Origin/CSRF, заголовок `idempotency-key` (8–80 символов); команды существующего отчёта принимают `expected_version`. Повтор того же тела возвращает существующий результат; другое тело под прежним ключом или устаревшая версия — 409. Все действия и выдача файлов журналируются транзакционно.

| Метод и путь | Контракт |
|---|---|
| GET `/options` | Собственные предложения аудитора, его отделы, отдельные рецензенты и потенциальные адресаты. Выбор не выдаёт прав; полная область повторно проверяется при создании и выпуске. |
| GET `/` | `q`, `state`, `offset=0..10000`, `limit=1..100`; `items/total/offset/limit` содержат только доступные отчёты. |
| POST `/` | `proposal_id`, необязательный `department_id` для выписки, `reviewer_id`, уникальные `recipients` (1–40), необязательный `previous_id` для исправления. Автор-аудитор во всех разделах исходного предложения; рецензент — отдельная личная учётная запись reviewer. |
| GET `/{id}` | Проверяемое содержание, зафиксированные доказательства, текущие флаги действий, метаданные выпуска и журнал с `offset/limit` (по умолчанию 25 событий). |
| GET `/{id}/revisions/{number}` | Историческая редакция только для чтения, автору/назначенному reviewer с актуальной ролью. Адресаты выпуска не получают старые черновики. |
| POST `/{id}/save` | `expected_version`, `content`, `note`; только автор в draft/returned, новая неизменяемая редакция, состояние draft. Снимок доказательств сохраняется прежним. |
| POST `/{id}/submit` | Автор; все разделы заполнены, reviewer актуален. Переход в in_review. |
| POST `/{id}/review` | Назначенный reviewer, fresh reauth; `outcome=accept/return`, `checked`, `note`. Принятие требует отметку проверки источников/выводов/адресатов. |
| POST `/{id}/release` | Тот же reviewer, fresh reauth, reviewed и рассмотренная текущая редакция, основание выпуска. Оба файла, manifest, хеши, событие и квитанция команды фиксируются одной транзакцией. После рендеринга повторно проверяются сессия и права автора, рецензента, всех адресатов. |
| GET `/{id}/export/pdf` или `/docx` | Только released, исходные неизменяемые байты, Content-Disposition attachment, no-store; контроль SHA-256 и обязательный журнал выдачи. |
| GET `/{id}/execution` | Отдельная текущая проекция исполнения. Дополнительно требует существующее адресное участие в предложении, выдаёт только разрешённую область. |

Публичные ошибки: 401 — сессия, 403 — CSRF/повторное подтверждение, 404 — недоступный/несуществующий объект, 409 — состояние/версия/отозванные полномочия выпуска, 422 — заполнение/состав адресатов, 503 — хранилище/контроль файла. Детали и чужие названия в отказах не раскрываются. Сохранённая адресность не заменяет текущие права. Выписка имеет свой снимок и отдельное рассмотрение: чужие разделы, общие межотдельские записи и личные заметки не включаются.

PDF/DOCX используют один упорядоченный набор проверенного содержания; файлы создаются один раз при выпуске, не при скачивании. Отдельная очередь уведомлений о стадиях отчёта пока не создаётся: reviewer работает со списком и фильтром «На проверке». Проверки: `backend/tests/test_reports.py`, `frontend/e2e/reports.spec.ts`; пользовательский порядок — [REPORT_WORKFLOW.md](REPORT_WORKFLOW.md).

REST `/api/v1` обслуживается FastAPI за Caddy на том же origin, что React. OpenAPI: `/api/openapi.json`, интерактивная документация: `/api/docs`. Схемы ответов определены в `backend/app/schemas.py`, запросов кадрового процесса — в `personnel.py`, входа — в `auth.py`. Сервер не возвращает ORM-модели аккаунтов/секреты.

## Вход и безопасность

Пароль → preauth session → подтверждённый TOTP/recovery → полноценная серверная сессия. Cookie HttpOnly/SameSite=Strict; Secure при HTTPS. CSRF-токен возвращается только своему браузеру; для POST/PUT требуется `X-CSRF-Token`, точный `Origin` и cookie текущей сессии. Login/reset также проверяют Origin и ограничения попыток. CORS с произвольными origins не включён.

Основной путь развёртывания — HTTPS через Caddy. Сейчас разрешён отдельный **loopback development HTTP** на `http://localhost:8080`, только синтетические данные. Production остаётся запрещён до проверки TLS Caddy/PostgreSQL/RabbitMQ и остальных требований этапа 4. API/PG/AMQP не публикуются отдельно в LAN. Frontend не обращается к БД или Gemini.

Каждый запрос проверяет актуальную сессию и сроки полномочий. `department_id` — фильтр, не разрешение. Чужой объект/отдел возвращает 404 без названия и идентификаторов закрытых объектов; отсутствие общей функциональной роли — 403. Access admin не получает бизнес-данные по своей технической роли. Просмотр старых событий/уведомлений/источников проверяет **текущие** права.

## Чтение

Списки возвращают `Page<T> = {items, total, offset, limit}`. `offset>=0`, `1<=limit<=100`, обычно limit=20; `q` ограничен 120 символами. Сортировка стабильная с идентификатором как дополнительным ключом. Count считается после scope и фильтров. Даты действия имеют ISO `YYYY-MM-DD`; timestamps содержат UTC offset; конец назначения не входит в интервал `[start,end)`.

| Операция | Доступ и параметры | Ответ |
|---|---|---|
| GET `/directory/employees` | Business scope; q, department_id, as_of, status=all/active/former/unassigned, kind=all/permanent/temporary, sort=name/name_desc/department | Page: человек, отдел, назначения с датами/должностями/единицами/основаниями, функции и function_count. Нет табельного номера |
| GET `/directory/employees/{id}` | Объект в scope; as_of | Та же карточка человека, без private fields |
| GET `/departments` | Business scope; q, as_of, offset, limit | Page: отделы и число уникальных действующих сотрудников, должностей, единиц, функций |
| GET `/functions` | Business scope; q, department_id, as_of, coverage | Page: документированные функции, статусы и исполнители |
| GET `/functions/{id}` | Объект в scope; as_of | Функция и история интервалов покрытия |
| GET `/documents` | Business scope; q, department_id, status=all/approved/registered_excerpt | Page: редакция, даты, SHA-256 и IDs доступных фрагментов. Оригиналы файлов пока не загружаются |
| GET `/sources/{id}` | Повторная проверка scope редакции | Неизменяемая выдержка/locator/hash. `organization` — текущая подпись UI; сохранённый текст/заголовок источника не переименовывается |
| GET `/overview` | Business scope; q, department_id, as_of, coverage=all/uncovered/unknown, offset, limit | counts + attention Page функций без исполнителя/с неопределённостью; unavailable tasks/reviews явно означает отсутствие реализации |
| GET `/organization` | Business scope; as_of | Полный snapshot для дополнительной схемы/инспектора; до 1000 объектов каждой основной коллекции, при превышении 413 вместо молчаливого усечения |
| GET `/personnel/events` | Business scope; department_id, q по должности, kind, offset, limit(default30) | Page неизменяемых событий с before/after, отдельными effective_date/registered_at |
| GET `/personnel/events/{id}` | Объект в scope | Событие с источником и снимками |
| GET `/notifications` | Только адресованные пользователю и всё ещё доступные; status=all/unread/read, offset, limit | Page уведомлений, включая контекст события |
| GET `/audit` | business — scoped; security — access_admin; category, q по типу события, offset, limit | Page: время, тип, object_id, department_id, payload, correlation_id и необязательное actor_display_name. Имена получаются только для авторов разрешенной страницы; null — имя не установлено. Сам просмотр журналируется |
| GET `/employees` | Совместимый HR-only подбор кандидатов конкретного department_id | Массив до 100 имён/ID; внутренний picker, пользовательский каталог использует paginated directory |
| GET `/employees/{id}?include_private=true` | Private field только HR того же отдела | Карточка с personnel_number; без флага поле отсутствует |
| GET `/system` | Подтверждённая сессия; счётчики очереди scoped | pending/failed, режим, key_configured boolean; значение ключа не возвращается |

`coverage`: all/covered/uncovered/unknown/abolished/not_effective. Для overview `all` означает оба требующих внимания состояния, а не все функции. `counts.employees` — число доступных записей людей, включая резерв/бывших; department.employee_count — уникальные **действующие** люди на выбранную дату. Человек, назначение, единица и аккаунт не приравниваются.

## Изменяющие операции

`POST /personnel/events` — HR с областью отдела и повторным подтверждением входа не старше 10 минут. Заголовок `Idempotency-Key` 16–80 символов; тело содержит action, seat_id, expected_version, даты и документальное основание. action: terminate/appoint/correct_termination. Пример тела на синтетических ID:

```json
{
  "action": "appoint",
  "seat_id": "ID-доступной-единицы",
  "employee_id": "ID-доступного-сотрудника",
  "effective_date": "2026-10-25",
  "end_date": "2026-11-01",
  "kind": "temporary",
  "expected_version": 2,
  "basis": {
    "title": "Учебный приказ № 25",
    "locator": "Пункт 1",
    "quote": "Назначить вымышленного сотрудника на временное замещение с указанной даты.",
    "document_date": "2026-10-24"
  }
}
```

Ответ `{event_id,appointment_id,replayed}`. Один ключ и идентичный запрос дают прежний результат без нового события; тот же ключ с другим телом — 409. `expected_version`, row locks и PostgreSQL exclusion защищают от конкурентных изменений/пересечения. Доменная запись, AuditEvent и OutboxRecord фиксируются в одной транзакции. Неизвестные JSON-поля запрещены; роль/права нельзя передать в теле.

`GET /organization/settings` возвращает `{name,display_name,version,can_edit}`. `name=null` означает незаполненное значение; подпись UI — «Название организации». `PUT /organization/settings` принимает только `{name,expected_version}`: HR/manager с действующей областью **всех** отделов и fresh reauth. Изменение версии и аудит атомарны; старые документы/события не переписываются. Отсутствие полномочий —403, устаревшая версия —409. Здесь нет кадрового побочного эффекта/уведомления, поэтому персональный outbox не создаётся.

`POST /notifications/{id}/read` — только свой доступный объект, повтор безопасен. Прочтение не означает согласие. `POST /outbox/retry-failed` — fresh reauth, HR/аудитор в scope; повторно ставит только допустимые failed записи. MFA/session/reset операции описаны в OpenAPI; их response-модели также входят в автоматическую проверку контракта.

## Ошибки и повторы

В интерфейсе от 29.10.2026 незаполненная настройка названия отображается в шапке как **OrgProekt** через единый `lib/branding.ts`; прежний серверный fallback и исторические источники не изменялись. Новый обзор использует существующие `/overview`, `/functions/{id}`, `/sources/{id}` и `/audit`. Смена даты/фильтра отменяет прежний клиентский запрос и скрывает старые данные. Последние события не фильтруются по дате обзора, что явно подписано в интерфейсе.

```json
{
  "code": "conflict",
  "message": "Запись уже изменена. Обновите данные перед повтором.",
  "detail": "Запись уже изменена. Обновите данные перед повтором.",
  "field_errors": [],
  "correlation_id": "серверный-uuid"
}
```

Статусы: 401 authentication_required; 403 forbidden; 404 not_found; 409 conflict; 413 request_too_large; 422 validation_error; 429 rate_limited (+Retry-After); 503 service_unavailable; 500 internal_error. `detail` сохранён как совместимое зеркало message. Correlation ID создаёт сервер и возвращает также в `X-Correlation-ID`; клиентский заголовок не принимается за доверенный ID. Валидация не отражает входные значения, пароль/токены/SQL/traceback.

Frontend сохраняет введённое при 409/422/сети, отменяет устаревшие чтения и ограничивает время запроса. Автоматического retry меняющих команд нет. Повтор неизменённого кадрового payload использует прежний key; после редактирования payload нужен новый key. После 409 версии требуется загрузить актуальные данные и явно повторить намерение.

## Автоматическая сверка и проверка

`backend/scripts/export_openapi.py` экспортирует фактический OpenAPI без подключения к БД и без реальных секретов. `--check` сравнивает его с `frontend/openapi.json`. Frontend генерирует TypeScript по этому файлу и использует его для typed client; CI должен выполнять обе проверки и TypeScript build.

PowerShell из корня:

```powershell
./scripts/docker.ps1 compose build api
./scripts/docker.ps1 compose -f compose.yml -f compose.test.yml --profile tests run --rm tests
./scripts/e2e.ps1
```

API-тесты работают с новой временной PostgreSQL-базой; основная БД не очищается. `test_api_contracts.py` проверяет реальные list/search/count/filter, карточку, отсутствие private fields, чужой/отозванный scope, response schemas, единый error envelope, настройки и optimistic conflict. Существующие тесты проверяют кадровой путь, idempotency, rollback audit, очередь и MFA. Расширение seed проверяется отдельным явным fixture с сохранением существующих строк. Фактические результаты запуска фиксируются в PROGRESS/EVALUATION, а не выводятся из одного наличия маршрутов.

Ограничения: перечни функций/отделов вычисляют производные статусы на сервере для ограниченного scoped корпуса до 1000 объектов; это не заявленная производительность для большой организации. RAG, analysis jobs, proposals/decisions, экспорт и production TLS ещё не реализованы; фиктивных маршрутов/успехов для них нет.

## Документы — первая часть этапа 2

Все пути ниже начинаются с `/api/v1`. Чтение требует MFA и актуального scope; запись — `document.write` (auditor/hr/department/manager в явно назначенном отделе). Загрузка и повтор обработки требуют reauth не старше 10 минут. У upload/retry/cancel действуют Origin+CSRF. Неизвестный или чужой объект возвращает одинаковый 404.

| Метод / путь | Контракт |
|---|---|
| GET `/documents/options` | Разрешённые для записи отделы, форматы/лимиты, OCR=false, external_calls_on_upload=false |
| POST `/documents/upload` | multipart/form-data; file, department_id, title, revision; optional document_id/expected_version, document_kind, document_date, effective_from, status=draft/unverified, synthetic, external_policy. Header Idempotency-Key, ответ 202: version_id/document_id/document_version/job_id/replayed |
| GET `/documents` | Прежняя пагинация; q по названию, department_id, status, processing, file_format; source_anchor_ids ограничены первыми 3, полный список — отдельным маршрутом |
| GET `/documents/{version_id}` | Метаданные без содержимого файла; текущее задание, предупреждения, счётчики, разрешённые действия |
| GET `/documents/{version_id}/versions` | История редакций документа с offset/limit |
| GET `/documents/{version_id}/fragments` | Точные anchors, q по подстроке цитаты, offset/limit, locator/page |
| GET `/documents/{version_id}/file` | Исходные байты: application/octet-stream, attachment, nosniff, CSP sandbox, no-store; только после структурной проверки (ready/partial/needs_ocr), иначе 409 |
| POST `/documents/{version_id}/retry` | JSON expected_version задания; до 3 попыток обработки, новая действующая сессия инициатора |
| POST `/documents/{version_id}/cancel` | JSON expected_version задания; отмена queued/processing, сохранённые оригинал и история остаются |
| GET `/sources/{anchor_id}` | Прежний контракт + nullable context: block_id/text/start/end/page/kind; quote == text[start:end] для извлечённых источников |

Одинаковые файл/метаданные/имя/пользователь и Idempotency-Key возвращают прежнюю загрузку. Другой payload с этим ключом — 409. Для редакции требуется актуальный `document_version`; конкурентная редакция — 409 без перезаписи. UI сохраняет поля и ключ при сетевой ошибке; автоматической повторной отправки нет. Общий лимит pending в отделе — 20 файлов. Парсер проверяет содержимое, не доверяет клиентскому MIME/расширению; ошибки парсера отражаются в job, а не как ложный успех.

Загрузка, исходные байты, версия, DocumentJob и audit атомарны. Извлечённые блоки/anchors, результат и audit тоже атомарны. Queue payload содержит только job_id. Worker повторно проверяет активность аккаунта, срок/отзыв/MFA сессии и scope до обработки и до записи. Отмена и перезапуск меняют lease; старый результат отбрасывается. Внешний Gemini не вызывается этим API.

## Этап 2: комплекты, сравнение и поиск

Префикс `/api/v1`, действующие сессия/MFA/scope; записи требуют CSRF, недавнего входа и актуальных прав. Закрытые объекты возвращают 404. Комплект/сравнение доступны только целиком: ограничение хотя бы одного отдела скрывает объект и из списков. Снимок прав в запуске — свидетельство прошлого, не разрешение на будущие чтения.

| Метод и маршрут | Контракт |
|---|---|
| POST `/corpora` | title, as_of, version_ids (1–20 уникальных редакций), declared_complete; Idempotency-Key. document.write во всех отделах. До 500 anchors, незавершённая обработка — 409. Возвращает неизменяемый снимок редакций, ограничений и digest. |
| GET `/corpora`, `/corpora/{id}` | Список с q/offset/limit (до 100), либо точный комплект; all-or-nothing ACL. |
| POST `/comparisons` | title, before_id, after_id, criteria; разные комплекты, document.write во всей области, Idempotency-Key. 202 с сохранённым запуском и заданием очереди. До трёх активных ручных заданий, иначе 429/Retry-After. |
| GET `/comparisons`, `/comparisons/{run_id}` | offset/limit до 50; оба снимка, критерии, версии, ограничения, job, can_review и сохранённая сверка реестра по области/датам/точным ссылкам. |
| GET `/comparisons/{run_id}/findings` | category, reviewed, offset/limit до 50; кандидаты, before/after Evidence, word diff, происхождение/метод/ограничения и последнее рассмотрение. |
| POST `/comparisons/{run_id}/findings/{finding_id}/reviews` | Только auditor во всей области. expected_version (0 для первого), decision confirmed/dismissed/needs_evidence, category, note; Idempotency-Key. Новая запись истории, 409 при чужом обновлении. Реестр функций не изменяется. |
| GET `/comparisons/{run_id}/findings/{finding_id}/reviews` | Сохраняемая история с автором/датой/версией и offset/limit до 50. |
| GET `/analysis-jobs/{job_id}` | Статус, безопасный error_code, attempts, version, can_retry/can_cancel, сводка; только полный текущий scope. |
| POST `/analysis-jobs/{job_id}/{retry\|cancel}` | expected_version; повтор до трёх попыток или отмена queued/processing; текущие document.write/сессия. Lease исключает позднюю запись. |
| GET `/search/status` | Разрешённые available/indexed_fragments, текущий index_key, метаданные модели, pending_version_ids (до 20), свои последние задания. local_model_available означает наличие локальных файлов; их SHA-256 проверяется при загрузке Encoder. |
| POST `/search/index` | version_ids (1–20), Idempotency-Key, document.write; до 500 фрагментов, 202 + durable job. Незавершённый парсер — 409; исходники не меняются. |
| GET `/search` | q (2–200 символов), mode hybrid/lexical/semantic; department_id/corpus_id/version_id/as_of, offset до 400, limit до 20. ACL и все фильтры до ранжирования. Возвращает mode_used, покрытие, ограничения и Evidence с рангами. |

`as_of` в поиске — известное начало действия редакции не позднее даты; неизвестные даты исключаются. Старые редакции автоматически не вытесняются новыми. Hybrid при недоступной модели явно возвращает lexical; semantic — 503. RRF-выдача ограничена 200 кандидатами каждой ветви (до 400 в объединении); total — кандидаты, а не число всех документов. Пустая выдача не доказывает отсутствия функции.

Синтетический пример тела POST `/corpora` (подставить разрешённый ID из `/documents`):

```json
{"title":"Учебный комплект до","as_of":"2026-10-28","version_ids":["разрешённый-uuid-редакции"],"declared_complete":false}
```

Ключ повторной команды должен оставаться тем же при повторе того же тела; изменённое тело с прежним ключом — 409. Retry/cancel используют версию задания и не являются созданием нового анализа. Для новых входных документов создаётся новый комплект/запуск. Все маршруты включены в экспорт OpenAPI и TypeScript-клиент.

## Этап 3: версии предложений, обсуждения и решения

Схемы находятся в `backend/app/change_schemas.py`; prefix — `/api/v1`. Успешные команды возвращают **200 и актуальную разрешённую проекцию ProposalDetail**. Для команд, кроме отметки прочтения, обязателен `Idempotency-Key` длиной 8–80 символов. Все команды существующего кейса также требуют `expected_version`, равный `ProposalDetail.version`. Это версия блокировки кейса; `current_version` — номер неизменяемой содержательной версии. Повтор ключа с тем же маршрутом, пользователем и телом возвращает существующий кейс без повторного бизнес-эффекта; изменённый запрос — 409. Старый `expected_version` — 409 с предложением обновить данные.

| Метод и маршрут | Контракт и ограничения |
|---|---|
| GET `/changes/options` | Разрешённые departments, author_departments, currently authorized participants с ролями по отделам, can_create. Технический администратор без бизнес-роли получает пустой набор. |
| GET `/changes/context?department_id=…` | Доступные source anchors, функции, должности, единицы и имена сотрудников; кадровые события только HR. Picker источников ограничен последними 500, событий — 100. Полные документы доступны через существующий реестр. |
| GET `/changes` | q до 180 символов, state, department_id, offset до 10000, limit 1–100 (default25). Page содержит только видимые кейсы; поиск выполняется по разрешённой проекции, включая названия/обоснования доступных разделов. |
| GET `/changes/{proposal_id}` | Текущая доступная версия, разделы, permissions, history и process_entries. Параметры history_offset/history_limit (default100, max100), versions_offset/versions_limit (default20, max50). Offset отсчитывается от новых записей; внутри страницы порядок хронологический. Возвращаются history_total/offset/limit и versions_total/offset/limit. Process_entries независимо содержит последние применимые отправки, ответы, решение, исполнение и итоговую аудиторскую проверку (`verified`) доступной версии, даже когда эти записи отсутствуют на выбранной странице общей истории. |
| POST `/changes` | Auditor создаёт title, effective_date, sections (1–12 уникальных отделов). Раздел: department_id, title, rationale и 1–30 items. Только собственные действующие auditor scopes. |
| POST `/changes/{proposal_id}/versions` | Полная новая ProposalRevision + change_reason + expected_version. История сохраняется, current_version увеличивается, состояние возвращается в draft, прежние ответы не применяются. После начала исполнения прежнюю модель не заменяют новой. |
| POST `/changes/{proposal_id}/send` | Владелец явно выбирает recipients[{department_id,user_id}], message и due_at с timezone, от текущего времени до 90 дней. Источники нужны у каждого действия. Повторная отправка тому же адресату той же версии —409. Роль manager определяет адресата управленческого решения; остальные — согласующие. |
| POST `/changes/{proposal_id}/messages`, `/notes` | expected_version, department_id, text, source_anchor_ids. Общие сообщения видны участникам этого раздела; notes доступны только автору-аудитору и не публикуются при отправке. Ссылки на источники проверяются отдельно. |
| POST `/changes/{proposal_id}/reviews` | Адресованный участник своего раздела: outcome=agree/return/reject/request_info, text, source_anchor_ids. Ответ привязан к неизменяемой версии; автор предложения не согласует собственную работу. |
| POST `/changes/{proposal_id}/decisions` | Fresh reauth, адресованный manager с manager scope всех разделов, не автор: approve/return/reject/accept_risk, reason, source_anchor_ids. Approve требует актуального согласия каждого назначенного согласующего и каждого отдела. Approve/accept_risk требуют источников. |
| POST `/changes/{proposal_id}/execution` | Fresh reauth, адресованный HR своего отдела: item_key, status=planned/completed, effective_date, note, source_anchor_id, optional personnel_event_id. План отдельно от факта; после всех completed —executed, после части —partially_executed. |
| POST `/changes/{proposal_id}/verify` | Владелец-аудитор, fresh reauth, note: только после executed. Аудитор не может независимо подтвердить собственное исполнение при совмещении HR-роли. |
| POST `/changes/{proposal_id}/cancel` | Владелец-аудитор, note: только до фактического исполнения. Состояние cancelled, история не удаляется; реестр и кадровые события не откатываются. |
| POST `/changes/{proposal_id}/reminders` | Владелец: recipient, kind=reminder/escalation, note. Только просроченное задание без ответа. Максимум три напоминания одному адресату версии/отдела с интервалом 24 часа; одна эскалация выбранному действующему manager. |
| POST `/changes/{proposal_id}/retry-delivery` | Владелец: notification_id, expected_version. Только исчерпанные 8 автоматических попыток текущей версии, ещё допустимые sender/recipient scopes. Максимум два ручных цикла; immutable history сохраняет повторы. |
| GET `/changes/notifications` | Page адресованных доставленных уведомлений с актуальными правами (limit1–100). Без текста закрытого раздела. |
| POST `/changes/notifications/{notification_id}/read` | Без тела; только собственное доступное доставленное уведомление. Повтор безопасен. Отдельный аудит; прочтение не создаёт ответа или согласия. |

Item имеет стабильный `key` в пределах раздела, kind, target_id, before/after, source_anchor_ids, requirements, candidate_employee_id, qualification_basis и qualification_source_ids. Для function kinds target_id — функция, для add_seat/position — должность, close_seat/temporary_substitution — единица, reporting — собственный отдел; для document ссылка на редакцию необязательна. Кандидат должен принадлежать тому же разрешённому отделу, иметь записанные требования, обоснование квалификации и источники. Это документированное предложение человека, не автоматическая рекомендация Gemini. Ответ содержит сохранённые impacts, включая снимок действующих закреплений/назначений на дату модели.

Членство в кейсе **не выдаёт доступ к отделу**. Участник видит только ранее адресованные ему версии и разрешённые отделы; скрытые разделы и их ID не возвращаются. Общий заголовок и текст общих управленческих оснований редактируются до нейтрального сообщения при частичной видимости. Новая ещё не отправленная версия не закрывает старую историю: получатель видит прежнюю адресованную версию в режиме чтения, с пояснением и выключенными действиями. Закрытая заметка не включается ни в общую историю получателя, ни в departmental business audit.

Completed требует approved/registered_excerpt источника своего отдела с известной датой действия не позднее исполнения; будущее исполнение пока только planned. Для temporary_substitution обязателен существующий, не заменённый исправлением OfficialPersonnelEvent вида appoint с temporary назначением нужной единицы, совпадающими датой/источником и выбранным кандидатом. Для остальных действий HR фиксирует документальное подтверждение; API **не переписывает автоматически функции, штат или подчинённость**. Отдельное кадровое изменение по-прежнему оформляется через `/personnel/events`.

ChangeEntry/Version/Section/Receipt append-only; изменение кейса, обязательный business audit по затронутым отделам и ChangeOutbox фиксируются атомарно. Приватная заметка даёт минимальный security audit без текста. Очередь содержит только outbox ID; worker повторно проверяет текущие полномочия отправителя и адресата. Доставка at-least-once с единственным уведомлением на entry/recipient; состояние доставки, read_at и replied_at различаются. Фоновая рассылка напоминаний не включена: каждый reminder/escalation — явная ограниченная команда. Gemini этим процессом не вызывается.
