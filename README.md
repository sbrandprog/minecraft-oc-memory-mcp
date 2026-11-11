# memory-mcp

Локальный MCP-сервис памяти для агента, управляющего роботом OpenComputers в
Minecraft 1.7.10. Хранит пространственные факты о мире и предпочтения игрока,
переживает перезапуски чата и сервера.

Сервис общается **только с агентом по MCP** и не обращается к Minecraft, NEI,
path-сервису или robot-сервису. Идентичность предметов приходит из NEI в виде
стабильного `item_uid`; сырые runtime ID (`265:0`, NBT) в память не попадают.

Полный план и схема: [PLAN.md](PLAN.md). Протокол работы агента:
[agent/AGENTS.md](agent/AGENTS.md).

## Быстрый старт

```sh
uv sync --extra dev
uv run memory-mcp        # stdio MCP server
uv run pytest -q         # тесты
```

По умолчанию база лежит в `data/memory.db`. Переопределяется через `MEMORY_DB`:

```sh
MEMORY_DB=/tmp/robot-memory.db uv run memory-mcp
```

## Подключение к OpenCode (V2)

```jsonc title="opencode.jsonc"
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "memory": {
        "type": "local",
        "command": [".venv/bin/python", "-m", "memory_mcp.server"],
        "cwd": ".",
        "environment": {
          "MEMORY_DB": "data/memory.db"
        }
      }
    }
  }
}
```

`cwd` — корень этого репозитория. В Code Mode инструменты будут доступны как
`tools.memory.open`, `tools.memory.brief` и т.д.; при `codemode: false` —
как `memory_open`, `memory_brief`, ...

## Инструменты

| MCP tool | Code Mode | Назначение |
|---|---|---|
| `open` | `tools.memory.open` | открыть сессию, получить `session_id` |
| `brief` | `tools.memory.brief` | core + ближайшие/релевантные POI под бюджет |
| `map` | `tools.memory.map` | ASCII/JSON карта POI |
| `remember_poi` | `tools.memory.remember_poi` | создать/обновить POI |
| `set_preference` | `tools.memory.set_preference` | pinned-факт или предпочтение |
| `remember_episode` | `tools.memory.remember_episode` | сохранить опыт: задача, трудности, урок |
| `recall_episodes` | `tools.memory.recall_episodes` | найти прошлый опыт по тексту/тегам |
| `forget_episode` | `tools.memory.forget_episode` | удалить эпизод |
| `update` | `tools.memory.update` | частично обновить POI |
| `forget` | `tools.memory.forget` | удалить POI по id или подстроке label |
| `stats` | `tools.memory.stats` | диагностика |

Эпизод — это переиспользуемый урок, а не лог шагов: `goal` (что просили),
`obstacles` (что помешало), `lesson` (как делать лучше), `outcome` и `tags`.
`brief(task=...)` автоматически добавляет подходящие уроки в `lessons`.

## Протокол агента (кратко)

1. `open(world_id, profile_id, robot_id)` → `session_id` (он же `ctx_id`).
2. `brief(task, pose)` в начале задачи. `session_id` можно не передавать:
   сервер помнит последнюю открытую сессию. Передавай его явно при переключении
   между роботами/профилями/мирами. `pose` — `{x, y, z, dim}`; `dim`
   необязателен и по умолчанию равен `0` (overworld). Поле `lessons` содержит
   релевантные уроки из прошлых задач.
3. Термин пользователя → `nei.resolve`; в память писать только `item_uid`.
4. Наблюдение → `remember_poi`, предпочтение → `set_preference`,
   «забудь» → `forget`.
5. Если задача дала переиспользуемый урок → `remember_episode`; если опыта
   не хватает → `recall_episodes` по описанию проблемы.

Инварианты:

- `world_id` неизменяем и уникален для пары «мир + сборка»;
- `session_id` задаёт мир/профиль/робота, аргументы модели его не переопределяют;
- никаких рецептов, маршрутов и алиасов в MVP;
- удаление — только явным `forget`; TTL чистится лениво.

Подробный цикл сессии, правила и типовые ошибки — в
[agent/AGENTS.md](agent/AGENTS.md).

## Тестирование

```sh
uv run pytest -q                          # юнит-тесты
uv run python scripts/mcp_smoke.py        # сквозной MCP-тест
```

По умолчанию smoke-тест пишет в `data/smoke.db`; путь переопределяется через
`SMOKE_DB`. Полный чек-лист, включая ручную проверку на агенте, — в
[docs/testing.md](docs/testing.md).

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `MEMORY_DB` | `data/memory.db` | путь к SQLite |
| `MEMORY_MIGRATIONS` | `migrations/` | каталог миграций |
| `MEMORY_CONTEXT_TTL_S` | `43200` | TTL `session_id`, секунды |
| `MEMORY_DEFAULT_RADIUS` | `64` | радиус `brief`/`map` по умолчанию |
| `MEMORY_DEFAULT_BUDGET_TOKENS` | `1200` | бюджет `brief` на POI |
| `MEMORY_MAX_RESULTS` | `200` | лимит записей в `map` |
| `MEMORY_LESSON_BUDGET_TOKENS` | `300` | бюджет `brief` на уроки |
| `MEMORY_MAX_LESSONS` | `3` | максимум уроков в `brief` |

## Структура

```
src/memory_mcp/
  server.py      # MCP-сервер и инструменты
  repo.py        # CRUD, видимость, идемпотентный merge
  spatial.py     # R*Tree и дистанция
  rank.py        # скоринг POI/эпизодов и бюджет токенов
  render.py      # ASCII-карта
  validate.py    # item_uid и идентификаторы
  schemas.py     # Pydantic-модели
  db.py          # SQLite, WAL, миграции
migrations/
  001_init.sql
  002_episodes.sql
tests/
```
