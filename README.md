# memory-mcp

Локальный MCP-сервис памяти для агента, управляющего роботом OpenComputers в
Minecraft 1.7.10 (GT New Horizons). Хранит пространственные факты о мире и
предпочтения игрока, переживает перезапуски чата и сервера.

Сервис общается **только с агентом по MCP** и не обращается к Minecraft, NEI,
path-сервису или robot-сервису. Идентичность предметов приходит из NEI в виде
стабильного `item_uid`; сырые runtime ID (`265:0`, NBT) в память не попадают.

Полный план и схема: [PLAN.md](PLAN.md).

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
| `update` | `tools.memory.update` | частично обновить POI |
| `forget` | `tools.memory.forget` | удалить по id или по подстроке label |
| `stats` | `tools.memory.stats` | диагностика |

## Протокол агента

1. `open(world_id, profile_id, robot_id)` → `session_id` (он же `ctx_id`).
2. `brief(task, pose)` в начале задачи. `session_id` можно не передавать:
   сервер помнит последнюю открытую сессию. Передавай его явно при переключении
   между роботами/профилями/мирами.
3. Пользовательский термин → `nei.resolve`; при неоднозначности уточнить у
   пользователя. В память писать только `item_uid` от NEI.
4. Наблюдение → `remember_poi`. Повторная встреча того же POI идемпотентно
   обновляет `last_seen_at`.
5. Предпочтение игрока → `set_preference`.
6. «Забудь» → `forget`.

Инварианты:

- `world_id` неизменяем и уникален для пары «мир + сборка»;
- `session_id` задаёт мир/профиль/робота, аргументы модели его не переопределяют;
- никаких рецептов, маршрутов, эпизодов и алиасов в MVP;
- удаление — только явным `forget`; TTL чистится лениво.

## Тестирование

### 1. Юнит-тесты

```sh
uv run pytest -q
```

### 2. Сквозной MCP-тест без агента

Поднимает сервер по stdio, проверяет список инструментов, запись/чтение POI,
preference, карту, отказ сырого `item_uid`, удаление и статистику:

```sh
uv run python scripts/mcp_smoke.py
```

По умолчанию используется `data/smoke.db`; путь переопределяется через
`SMOKE_DB`:

```sh
SMOKE_DB=/tmp/memory-smoke.db uv run python scripts/mcp_smoke.py
```

### 3. Тест на агенте (OpenCode)

1. Подключить сервер по конфигу выше и проверить статус:

   ```sh
   opencode mcp list
   ```

   В списке должно быть `✓ memory  connected`.

2. В TUI открыть `/mcps` и убедиться, что у сервера видны 8 инструментов.

3. Проверить happy path. Напишите агенту:

   > Открой сессию памяти для world_id=test-world, profile_id=me, robot_id=r1.
   > Запомни сундук с железом на (10,64,10) в overworld, item_uid=ore:ingotIron.
   > Затем покажи brief для позиции (10,64,12).

   Ожидаемо: агент вызовет `memory_open`, `memory_remember_poi`, `memory_brief`
   и покажет `poi:1` с `dist=2`.

4. Проверить персистентность: перезапустите OpenCode, снова откройте тот же
   `world_id` и попросите brief. POI должен остаться.

5. Проверить изоляцию: откройте `robot_id=r2` в том же мире. POI со
   `scope=robot` от `r1` не должен появиться, а `scope=world` — должен.

6. Проверить валидацию: попросите записать POI с `item_uid="265:0"`.
   Агент должен получить ошибку инструмента и не создать запись.

7. Проверить карту: попросите `memory_map` с радиусом 32. В ответе должна быть
   ASCII-сетка с легендой и списком POI.

Если агент «забывает» вызывать память, добавьте в `AGENTS.md` проекта агента
короткое правило: в начале задачи вызывать `memory_brief`, при наблюдении —
`memory_remember_poi`, при предпочтении — `memory_set_preference`.



## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `MEMORY_DB` | `data/memory.db` | путь к SQLite |
| `MEMORY_MIGRATIONS` | `migrations/` | каталог миграций |
| `MEMORY_CONTEXT_TTL_S` | `43200` | TTL `session_id`, секунды |
| `MEMORY_DEFAULT_RADIUS` | `64` | радиус `brief`/`map` по умолчанию |
| `MEMORY_DEFAULT_BUDGET_TOKENS` | `1200` | бюджет `brief` |
| `MEMORY_MAX_RESULTS` | `200` | лимит записей в `map` |

## Структура

```
src/memory_mcp/
  server.py      # MCP-сервер и инструменты
  repo.py        # CRUD, видимость, идемпотентный merge
  spatial.py     # R*Tree и дистанция
  rank.py        # скоринг и бюджет токенов
  render.py      # ASCII-карта
  validate.py    # item_uid и идентификаторы
  schemas.py     # Pydantic-модели
  db.py          # SQLite, WAL, миграции
migrations/001_init.sql
tests/
```

Локальные файлы сборки (инстанс GTNH, jar-ы, архивы) лежат в `local/` и
игнорируются git.
