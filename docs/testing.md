# Тестирование memory-mcp

## 1. Юнит-тесты

```sh
uv run pytest -q
```

## 2. Сквозной MCP-тест без агента

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

## 3. Тест на агенте (OpenCode)

1. Подключить сервер по конфигу из README и проверить статус:

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
