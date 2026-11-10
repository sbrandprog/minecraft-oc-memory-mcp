# План: memory-mcp для агента-робота OpenComputers (Minecraft 1.7.10)

## 1. Цель

Отдельный локальный MCP-сервис памяти для агента (opencode / Pi), который управляет
роботом OpenComputers в Minecraft 1.7.10 (сборка GT New Horizons). Сервис хранит
пространственные факты о мире и предпочтения игрока, переживает перезапуски чата и
сервера, и общается с агентом **только по MCP**.

## 2. Границы

- Сервис не обращается к Minecraft, NEI, path-сервису и robot-сервису.
- Единственный источник наблюдений о мире — агент, который передаёт их в вызовах.
- Единственный источник идентичности предметов — `nei-mcp`: память хранит
  непрозрачный стабильный `item_uid`, но никогда не хранит сырые runtime ID
  (`id:meta`, NBT).
- Pathfinding — зона `path-mcp`; память не планирует маршруты.
- Рецепты — зона `nei-mcp`; память не кэширует рецепты.

```
opencode / Pi ──MCP──► memory-mcp ──► SQLite (один файл, WAL)
```

## 3. Область MVP

В памяти есть только:

- `core` — pinned-факты: база, waypoints, предпочтения игрока, текущее назначение;
- `poi` — точки интереса: сундуки, руды, машины, порталы, опасности, база, waypoint;
- `brief` / `map` — выдача контекста агенту.

Сознательно **не входит** в MVP:

- эпизоды и уроки (обучение на ошибках);
- маршруты и статистика путей;
- алиасы и жаргон;
- кэш рецептов;
- эмбеддинги и семантический поиск;
- FTS5 (при необходимости добавляется позже);
- аренда POI между роботами (`claim`).

## 4. Изоляция

| Ось | Что задаёт | Граница |
|---|---|---|
| `world_id` | экземпляр мира + сборка | жёсткая, данные не пересекаются |
| `profile_id` | игрок/команда | мягкая, персонализация |
| `robot_id` | робот-актор | приватная рабочая память |
| `scope` | видимость записи | `world` / `profile` / `robot` |

- `world_id` неизменяем и уникален для пары «мир + сборка». При смене пака — новый
  `world_id` или очистка памяти.
- Границу задаёт `ctx_id`, выданный `memory_open`; аргументы LLM не могут её
  переопределить.
- `owner`: `''` для `world`, `p:<profile>` для `profile`, `r:<robot>` для `robot`.

Правило: **актора разделяем, мир делим**. POI и опасности видны всем в мире; текущая
задача и приватные факты — только роботу.

## 5. Хранилище

Один SQLite-файл, WAL. Шесть таблиц:

| Таблица | Роль |
|---|---|
| `meta` | версия схемы |
| `world` | реестр миров |
| `context` | выданные `ctx_id` |
| `poi` | точки интереса |
| `poi_rtree` | 4D-индекс `dim/x/y/z` |
| `core` | pinned-факты и предпочтения |

### 5.1. DDL

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE meta (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE world (
  world_id     TEXT PRIMARY KEY,
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL
);

CREATE TABLE context (
  ctx_id       TEXT PRIMARY KEY,
  world_id     TEXT NOT NULL REFERENCES world(world_id),
  profile_id   TEXT NOT NULL,
  robot_id     TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL,
  revoked      INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);

CREATE INDEX idx_context_lookup ON context(world_id, profile_id, robot_id);

CREATE TABLE poi (
  id           INTEGER PRIMARY KEY,
  world_id     TEXT NOT NULL REFERENCES world(world_id),
  scope        TEXT NOT NULL CHECK (scope IN ('world', 'profile', 'robot')),
  owner        TEXT NOT NULL DEFAULT '',
  dim          INTEGER NOT NULL,
  x            INTEGER NOT NULL,
  y            INTEGER NOT NULL CHECK (y BETWEEN 0 AND 255),
  z            INTEGER NOT NULL,
  radius       INTEGER NOT NULL DEFAULT 0 CHECK (radius >= 0),
  kind         TEXT NOT NULL,
  label        TEXT NOT NULL,
  item_uid     TEXT NOT NULL DEFAULT '',
  tags         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL CHECK (source IN ('observed', 'told', 'inferred')),
  confidence   REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  ttl_at       INTEGER,
  CHECK (item_uid = '' OR (instr(item_uid, ':') > 0 AND item_uid NOT GLOB '[0-9]*')),
  UNIQUE (world_id, dim, x, y, z, scope, owner, kind, item_uid)
);

CREATE INDEX idx_poi_scope ON poi(world_id, scope, owner);
CREATE INDEX idx_poi_item  ON poi(world_id, item_uid) WHERE item_uid <> '';
CREATE INDEX idx_poi_kind  ON poi(world_id, kind);

CREATE VIRTUAL TABLE poi_rtree USING rtree(
  id,
  dim_min, dim_max,
  x_min, x_max,
  y_min, y_max,
  z_min, z_max
);

CREATE TRIGGER poi_ai AFTER INSERT ON poi BEGIN
  INSERT INTO poi_rtree(id, dim_min, dim_max, x_min, x_max, y_min, y_max, z_min, z_max)
  VALUES (new.id, new.dim, new.dim,
          new.x - new.radius, new.x + new.radius,
          new.y - new.radius, new.y + new.radius,
          new.z - new.radius, new.z + new.radius);
END;

CREATE TRIGGER poi_au AFTER UPDATE OF dim, x, y, z, radius ON poi BEGIN
  DELETE FROM poi_rtree WHERE id = old.id;
  INSERT INTO poi_rtree(id, dim_min, dim_max, x_min, x_max, y_min, y_max, z_min, z_max)
  VALUES (new.id, new.dim, new.dim,
          new.x - new.radius, new.x + new.radius,
          new.y - new.radius, new.y + new.radius,
          new.z - new.radius, new.z + new.radius);
END;

CREATE TRIGGER poi_ad AFTER DELETE ON poi BEGIN
  DELETE FROM poi_rtree WHERE id = old.id;
END;

CREATE TABLE core (
  world_id   TEXT NOT NULL REFERENCES world(world_id),
  scope      TEXT NOT NULL CHECK (scope IN ('world', 'profile', 'robot')),
  owner      TEXT NOT NULL DEFAULT '',
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 1.0 CHECK (importance BETWEEN 0 AND 1),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (world_id, scope, owner, key)
);

CREATE INDEX idx_core_lookup ON core(world_id, scope, owner);
```

### 5.2. Валидация `item_uid`

```python
UID_RE = re.compile(r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
RAW_RE = re.compile(r"^\d+(:\d+)?$")
```

- `item_uid` приходит только от NEI и сохраняется verbatim;
- пустая строка разрешена для не-item POI и для «NEI ещё не готов»;
- сырые `265:0`, NBT, локально собранные `item_key` запрещены.

## 6. MCP API

Имена MCP-инструментов короткие, потому что клиент добавляет имя сервера:
в OpenCode это `memory_open`, `memory_brief`, ... (или `tools.memory.brief` в
Code Mode). Python-функции внутри пакета называются `memory_*`.

```python
memory_open(world_id, profile_id, robot_id) -> {ctx_id, world_id, profile_id, robot_id}

memory_brief(ctx_id, task=None, pose=None, item_uid=None, kind=None,
             radius=64, budget_tokens=1200) -> Brief

memory_map(ctx_id, dim, center, radius, format="ascii") -> MapResult

memory_remember_poi(ctx_id, record: PoiRecord) -> {id, merged}
memory_set_preference(ctx_id, record: PreferenceRecord) -> {key}

memory_update(ctx_id, poi_id, patch: PoiPatch) -> Poi
memory_forget(ctx_id, poi_id=None, query=None) -> {deleted}

memory_stats(ctx_id) -> {poi_total, by_kind, by_scope, db_bytes, world_id}
```

`Brief`:

```json
{
  "core": [{"key": "base", "value": "overworld (120,64,-340)"}],
  "nearby": [{
    "id": "poi:17", "kind": "chest", "label": "iron",
    "item_uid": "ore:ingotIron", "dim": 0,
    "x": 131, "y": 63, "z": -352,
    "dist": 14, "source": "observed", "confidence": 0.9
  }],
  "relevant": [],
  "stats": {"returned": 1, "used_tokens": 412}
}
```

## 7. Потоки

### Запись

1. Проверить `ctx_id`, получить `world/profile/robot`.
2. Валидировать `item_uid`, координаты, `y ∈ [0, 255]`.
3. Найти кандидатов через `poi_rtree` в пределах `radius + tolerance` с тем же
   `kind`, `item_uid`, `scope`, `owner`.
4. Если есть — merge: `label`, `tags`, `confidence`, `last_seen_at`.
5. Иначе insert; уникальный индекс ловит точные повторы.
6. Предпочтение — upsert в `core` по `(world, scope, owner, key)`.

### Чтение

1. `ctx_id` → контекст, проверить TTL и `revoked`.
2. Всегда добавить видимые `core` (по `importance`).
3. Если задан `pose` — bbox по `poi_rtree`, затем точная дистанция
   `sqrt(dx² + dz²) + 2|dy|`.
4. Фильтр по `kind`, `item_uid`, `scope`, `ttl_at`.
5. Ранжирование: `source_weight + confidence + recency + distance_decay`;
   точное совпадение `item_uid` — буст.
6. Обрезать под `budget_tokens`, вернуть `id` записей.

## 8. Протокол агента

1. Старт сессии: `memory_open` → `ctx_id`.
2. Старт задачи: `memory_brief(task, pose)`.
3. Пользовательский термин → `nei.resolve`; неоднозначно → уточнить у пользователя.
4. Наблюдение → `memory_remember_poi` с UID от NEI.
5. Предпочтение игрока → `memory_set_preference`.
6. Повторная встреча POI → idempotent `memory_remember_poi` (обновляет `last_seen_at`).
7. «Забудь» → `memory_forget`.

## 9. Инварианты

1. В памяти нет сырых runtime ID предметов — только `item_uid` от NEI.
2. `world_id` неизменяем; смена пака — новый мир или очистка.
3. Память не вызывает другие сервисы и Minecraft.
4. Видимость и мир берутся из `ctx_id`, а не из аргументов модели.
5. `memory_remember_poi` идемпотентен.
6. Удаление — только явный `memory_forget`; TTL чистится лениво.
7. Никаких рецептов, маршрутов, эпизодов и алиасов в MVP.

## 10. Структура проекта

```
.
├── .gitignore
├── PLAN.md
├── pyproject.toml
├── migrations/
│   └── 001_init.sql
├── src/memory_mcp/
│   ├── __init__.py
│   ├── server.py      # FastMCP, регистрация tools
│   ├── schemas.py     # Pydantic-модели
│   ├── db.py          # соединение, WAL, миграции
│   ├── repo.py        # CRUD, visibility, merge
│   ├── spatial.py     # R*Tree-запросы, дистанция
│   ├── rank.py        # скоринг и бюджет токенов
│   ├── render.py      # ASCII-карта
│   ├── validate.py    # item_uid, координаты
│   └── config.py      # путь к БД, лимиты
├── tests/
└── data/              # runtime, в .gitignore
```

## 11. Дорожная карта

1. Схема БД, миграции, валидация.
2. `memory_open`, `memory_remember_poi`, `memory_set_preference`.
3. `poi_rtree`-поиск, `memory_brief`, ранжирование и бюджет.
4. `memory_map`, `memory_update`, `memory_forget`, `memory_stats`.
5. Тесты: UID, видимость, дедуп, TTL, brief.
6. Интеграция с агентом по MCP, протокол в `AGENTS.md` (в репозитории агента).

Позже, без ломки схемы: `episode`/`lesson`, `route_stats`, FTS5, `embedding`,
`claim`.
