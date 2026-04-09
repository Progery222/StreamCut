

**CLAUDE**

GLOBAL INSTRUCTIONS

Справочник по настройке и использованию

автономного режима, MCP серверов и скилов

*v1.0*

# **СТИЛЬ РАБОТЫ**

Основные принципы взаимодействия с Claude при работе над кодом:

* Отвечай кратко и по делу

* Не добавляй комментарии, docstring и типы к коду который не изменял

* Не рефакторь код за пределами задачи

* Не создавай лишних файлов и абстракций

# **GIT**

Правила работы с системой контроля версий:

* Всегда спрашивай перед git push

* Никогда не используй \--force без явного разрешения

* Никогда не пропускай хуки (--no-verify)

# **БЕЗОПАСНОСТЬ**

* Не коммить файлы с секретами (.env, credentials, API ключи)

* Предупреждай если видишь секреты в коде

# **ИНСТРУМЕНТЫ**

* Используй Read/Edit/Grep/Glob вместо bash-команд cat/grep/find

* Запускай независимые операции параллельно

# **АВТОНОМНЫЙ РЕЖИМ**

## **Запуск: один шаг**

Выполнить первый незакрытый шаг из PROGRESS.md:

claude \-p "прочитай PROGRESS.md, найди первый незакрытый шаг \[ \], реализуй его, отметь \[x\], запусти проверки" \--max-turns 40

## **Запуск: несколько шагов**

Выполнить все незакрытые шаги в текущей фазе:

claude \-p "прочитай PROGRESS.md, реализуй все незакрытые шаги \[ \] по одному, отмечай \[x\] и проверяй" \--max-turns 100

## **Флаги запуска**

| Флаг | Назначение |
| :---- | :---- |
| \--max-turns 40 | Лимит итераций (защита от бесконечного цикла) |
| \--allowedTools "Read,Edit,..." | Ограничение инструментов (без MCP) |
| \--output-format stream-json | JSON вывод для скриптов |
| \--verbose | Видеть все шаги агента |

## **Параллельная работа**

Можно запускать несколько агентов одновременно. Просто укажите в промпте:

реализуй шаги 2.1, 2.2 и 2.3 параллельно — запусти отдельный агент на каждый

Claude сам запустит Agent tool несколько раз одновременно.

## **Требования**

Чтобы автономный режим работал корректно, необходимо:

* **CLAUDE.md** в корне проекта — Claude знает стек и команды

* **PROGRESS.md** с чеклистом шагов \[ \] / \[x\]

* **Автоматические проверки** с ненулевым exit code при ошибках

* Каждый шаг должен быть атомарным и проверяемым

**Примеры команд проверки**

| Стек | Команда |
| :---- | :---- |
| TypeScript | tsc \--noEmit |
| Python | ruff check . && pytest |
| JS/TS | biome check . |

## **Когда НЕ подходит**

* Нет тестов / линтера — Claude не знает что готово

* Шаги зависят от внешних API без моков

* Нужны решения по архитектуре (сначала обсуди, потом запускай)

# **ШАБЛОН PROGRESS.md**

Структура файла для отслеживания прогресса:

\# PROGRESS

\#\# Фаза 1: \[Название\]

\- \[ \] 1.1 \[Задача\] — 🧪 \`команда проверки\`

\- \[ \] 1.2 \[Задача\] — 🧪 \`команда проверки\`

\- \[x\] 1.3 \[Выполнено\]

# **СОЗДАНИЕ CLAUDE.md**

При старте нового проекта создайте CLAUDE.md в корне. Пишите только то, что уникально для проекта.

**Структура шаблона**

| Раздел | Содержимое |
| :---- | :---- |
| Стек | Язык, фреймворк, БД, основные зависимости |
| Структура | src/ — что тут, tests/ — что тут |
| Команды | Запуск, тесты, билд, линтер |
| Соглашения | Правила именования, ограничения |
| Переменные окружения | .env.example — шаблон |

**Правила**

* Не дублируй глобальный CLAUDE.md

* Пиши только то, что Claude не может вывести из кода

* Обновляй при изменении архитектуры или стека

# **ДОСТУПНЫЕ MCP СЕРВЕРЫ**

Все серверы доступны в любом проекте автоматически. Просто попросите — Claude сам вызовет нужный.

| Сервер | Назначение |
| :---- | :---- |
| context7 | Актуальная документация любой библиотеки |
| docker | Управление контейнерами: запуск, сборка, inspect |
| playwright | Автоматизация браузера: скрейпинг, тесты |
| shadcn | Компоненты shadcn/ui: паттерны, API |
| firecrawl | Парсинг сайтов в markdown |
| magic | Генерация UI компонентов 21st.dev |
| Neon | PostgreSQL: создание БД, миграции, SQL |

# **ДОСТУПНЫЕ SKILLS**

Скилы активируются командой /название-скила или автоматически по контексту.

## **Frontend / Вайбкодинг**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| frontend-design | /frontend-design | Генерация UI, смелые дизайн-решения |
| web-artifacts-builder | /web-artifacts-builder | React \+ Tailwind \+ shadcn/ui |
| react-expert | /react-expert | Хуки, паттерны, оптимизация |
| nextjs-developer | /nextjs-developer | App Router, Server Components |
| typescript-pro | /typescript-pro | Сложные типы, generics |
| webapp-testing | /webapp-testing | Тесты UI через Playwright |

## **Python / Backend**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| fastapi-expert | /fastapi-expert | Async API, Pydantic, DI |
| django-expert | /django-expert | Django \+ DRF, ORM, auth |
| python-pro | /python-pro | Type hints, async, оптимизация |
| setting-up-python-libraries | /setting-up-python-libraries | Новый проект: uv, ruff, pytest |
| improving-python-code-quality | /improving-python-code-quality | Ruff, mypy, рефакторинг |
| testing-python-libraries | /testing-python-libraries | Pytest, fixtures, property |
| designing-python-apis | /designing-python-apis | API design, error handling |
| auditing-python-security | /auditing-python-security | Bandit, pip-audit, secrets |
| optimizing-python-performance | /optimizing-python-performance | Профилирование, benchmarks |
| building-python-clis | /building-python-clis | Click/Typer, shell completion |

## **Go**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| golang-pro | /golang-pro | Горутины, каналы, gRPC, generics |
| go-tool | /go-tool | golangci-lint, GoReleaser, CI |

## **Архитектура / Инфраструктура**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| microservices-architect | /microservices-architect | Паттерны микросервисов |
| api-designer | /api-designer | REST, OpenAPI, версионирование |
| postgres-pro | /postgres-pro | Запросы, индексы, репликация |
| kubernetes-specialist | /kubernetes-specialist | K8s, Helm, деплой |
| terraform-engineer | /terraform-engineer | IaC, multi-cloud |
| devops-engineer | /devops-engineer | CI/CD, деплой |
| mcp-builder | /mcp-builder | MCP серверы под любой API |

## **Качество / Дебаг**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| test-master | /test-master | Стратегия тестирования |
| debugging-wizard | /debugging-wizard | Системный дебаг |
| systematic-debugging | /systematic-debugging | Пошаговый дебаг с гипотезами |
| code-reviewer | /code-reviewer | Детальное ревью кода |
| secure-code-guardian | /secure-code-guardian | OWASP, уязвимости |

## **Superpowers (планирование и агенты)**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| brainstorming | /brainstorm | Генерация идей и решений |
| writing-plans | /write-plan | Написание плана |
| executing-plans | /execute-plan | Выполнение плана по шагам |
| test-driven-development | /tdd | TDD red-green-refactor |
| subagent-driven-development | /subagent | Параллельная работа агентами |
| using-git-worktrees | /worktree | Изолированная работа в worktrees |

## **Документы**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| pdf | /pdf | Создание, парсинг, merge PDF |
| docx | /docx | Word документы |
| pptx | /pptx | PowerPoint презентации |
| xlsx | /xlsx | Excel таблицы с формулами |

## **Data / ML**

| Скил | Команда | Когда использовать |
| :---- | :---- | :---- |
| pandas-pro | /pandas-pro | DataFrames, очистка, агрегация |
| rag-architect | /rag-architect | RAG, vector DB, embeddings |
| prompt-engineer | /prompt-engineer | LLM промпты, evaluation |

# **РЕКОМЕНДУЕМЫЕ КОМБО**

Оптимальные цепочки скилов для типовых сценариев:

| Сценарий | Цепочка скилов |
| :---- | :---- |
| Новый фронт-проект | /frontend-design → UI → shadcn MCP → /webapp-testing |
| Новый Python API | /setting-up-python-libraries → /fastapi-expert → /designing-python-apis → /testing-python-libraries |
| Новый Go сервис | /golang-pro → /api-designer → /go-tool |
| Планирование фичи | /brainstorm → /write-plan → /execute-plan |
| Дебаг | /systematic-debugging → /debugging-wizard |

