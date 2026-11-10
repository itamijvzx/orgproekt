# Схема базы данных OrgProekt

OrgProekt хранит данные в PostgreSQL. На схемах ниже показаны основные таблицы. Стрелка ведёт к таблице, в которой находится внешний ключ. Подпись на стрелке показывает его поле.

## Сотрудники и назначения

```mermaid
flowchart TD
    departments["Отделы<br/>departments"]
    employees["Сотрудники<br/>employees"]
    positions["Должности<br/>positions"]
    staffing_seats["Штатные единицы<br/>staffing_seats"]
    appointments["Назначения<br/>appointments"]
    source_anchors["Пункты документов<br/>source_anchors"]
    personnel_events["Кадровые события<br/>personnel_events"]

    departments -->|department_id| employees
    departments -->|department_id| positions
    positions -->|position_id| staffing_seats
    employees -->|employee_id| appointments
    staffing_seats -->|seat_id| appointments
    source_anchors -->|source_anchor_id| appointments
    appointments -->|appointment_id| personnel_events
```

Назначение фиксирует, кто занимает конкретную штатную единицу, на какой срок и на основании какого пункта документа. PostgreSQL запрещает пересекающиеся периоды назначений на одну единицу. Кадровые события хранят историю регистрации, прекращения и исправления назначений.

## Документы и функции

```mermaid
flowchart TD
    documents["Карточки документов<br/>documents"]
    document_versions["Редакции документов<br/>document_versions"]
    document_originals["Файлы DOCX и PDF<br/>document_originals"]
    source_anchors["Пункты и цитаты<br/>source_anchors"]
    documented_functions["Документированные функции<br/>documented_functions"]
    function_assignments["Закрепления функций<br/>function_assignments"]

    documents -->|document_id| document_originals
    document_versions -->|version_id| document_originals
    document_versions -->|document_version_id| source_anchors
    source_anchors -->|source_anchor_id| documented_functions
    documented_functions -->|function_id| function_assignments
```

`document_originals` хранит файл и связывает карточку документа с конкретной редакцией. `source_anchors` хранит точное место и цитату из редакции. Документированная функция ссылается на этот источник. Закрепление функции указывает подразделение и может ссылаться на конкретную должность.

## Комплекты и сравнение

```mermaid
flowchart TD
    document_versions["Редакции документов<br/>document_versions"]
    document_corpora["Комплекты документов<br/>document_corpora"]
    corpus_members["Редакции в комплекте<br/>corpus_members"]
    comparison_runs["Запуски сравнения<br/>comparison_runs"]
    comparison_findings["Замечания<br/>comparison_findings"]
    finding_reviews["Решения по замечаниям<br/>finding_reviews"]

    document_versions -->|version_id| corpus_members
    document_corpora -->|corpus_id| corpus_members
    document_corpora -->|before_id / after_id| comparison_runs
    comparison_runs -->|run_id| comparison_findings
    comparison_findings -->|finding_id| finding_reviews
```

Каждый запуск сравнения фиксирует два комплекта «до» и «после». Результаты и решения аудитора по ним сохраняются отдельными записями. Для поиска по смыслу таблица `search_chunks` связывает фрагменты цитат с векторами pgvector.

## Учётные записи и доступ

```mermaid
flowchart TD
    user_accounts["Учётные записи<br/>user_accounts"]
    departments["Отделы<br/>departments"]
    role_assignments["Назначения прав<br/>role_assignments"]
    access_requests["Запросы на доступ<br/>access_requests"]
    grant_revocations["Отзывы прав<br/>grant_revocations"]
    browser_sessions["Сеансы входа<br/>browser_sessions"]

    user_accounts -->|user_id| role_assignments
    departments -->|department_id| role_assignments
    user_accounts -->|user_id| access_requests
    role_assignments -->|assignment_id| access_requests
    role_assignments -->|assignment_id| grant_revocations
    user_accounts -->|user_id| browser_sessions
```

Назначение права хранит роль, период действия и отдел, если право ограничено отделом. После одобрения запроса в нём сохраняется ссылка на выданное право. Отзыв создаёт отдельную запись и сохраняет историю назначения.

## Предложения и отчёты

```mermaid
flowchart TD
    change_proposals["Предложения изменений<br/>change_proposals"]
    change_versions["Редакции предложений<br/>change_versions"]
    change_sections["Разделы по отделам<br/>change_sections"]
    change_entries["Обсуждение и решения<br/>change_entries"]
    reports["Отчёты<br/>reports"]
    report_revisions["Редакции отчётов<br/>report_revisions"]
    report_releases["Выпуски PDF и DOCX<br/>report_releases"]

    change_proposals -->|proposal_id| change_versions
    change_versions -->|version_id| change_sections
    change_versions -->|version_id| change_entries
    change_proposals -->|proposal_id| reports
    reports -->|report_id| report_revisions
    reports -->|report_id| report_releases
```

Предложение сохраняет редакции и привязанные к ним обсуждения и решения. Редакция отчёта содержит снимок предложения и источников. В записи выпуска хранятся номер выпущенной редакции, файлы PDF и DOCX и их контрольные суммы.

## Журнал действий и исходники

`audit_events` хранит события аудита с временем, типом действия и идентификаторами участника и объекта, когда они доступны. База также хранит задания обработки, уведомления и записи, по которым сервер распознаёт повторные команды.

Определения таблиц можно найти в `models.py` и файлах `*_models.py` в [каталоге приложения](../backend/app), а историю изменений структуры — в [миграциях Alembic](../backend/migrations/versions). Правила работы с датами, правами и версиями описаны в [доменной модели](DOMAIN_MODEL.md).
