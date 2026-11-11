# AGENTS.md

## What this repo is

`memory-mcp` — Python MCP server that stores spatial memory (POIs, preferences)
for an OpenComputers robot agent. It talks only to an MCP client; it does not
call Minecraft, NEI, or path/robot services. Python 3.12, `src/` layout, SQLite.

Do not confuse `agent/AGENTS.md` with repo guidance: it is a **product artifact**
(the prompt/instructions for the in-game robot agent), not instructions for
coding agents. Don't edit it unless the task is about that agent protocol.

## Commands

```sh
uv sync --extra dev
uv run pytest -q                              # all tests
uv run pytest tests/test_memory.py::test_ttl_expires_poi -q
uv run python scripts/mcp_smoke.py            # end-to-end MCP over stdio
uv run memory-mcp                             # run server (stdio)
uv run python -m memory_mcp.server            # equivalent entrypoint
```

- `uv` may be absent from non-login shells; it is at `~/.local/bin/uv`. The
  existing `.venv/bin/python -m pytest` also runs the suite.
- No ruff/mypy/formatter config is committed; do not assume a lint step exists.
- The smoke script deletes and recreates its DB (`data/smoke.db`, override with
  `SMOKE_DB`) and spawns the server with `sys.executable -m memory_mcp.server`.

## Architecture

- `src/memory_mcp/server.py` — MCP wiring and the 11 tools. It imports
  `MCPServer` from `mcp.server.mcpserver` (MCP v2 API, **not** `FastMCP`).
  Tool names registered with MCP are short: `open`, `brief`, `remember_poi`,
  ...; `memory_*` is only the client-side/Code Mode prefix.
- `repo.py` — all business logic (contexts, merge, visibility, brief, map,
  stats). `server.py` is a thin adapter; put behavior here.
- `spatial.py` — R*Tree queries, owner visibility, weighted distance.
- `db.py` / `migrations/` — SQLite setup and numbered SQL migrations.
- `schemas.py` / `validate.py` — Pydantic models and `item_uid` validation.
- `rank.py` / `render.py` — scoring/token budget and ASCII map.
- `tests/conftest.py` builds a `MemoryRepo` on a temp DB with the repo's
  `migrations/`. Tool-level tests monkeypatch `server._repo`.

## Non-obvious behavior to preserve

- Default DB is `Path.cwd() / "data/memory.db"`, not repo-root-relative.
  `MEMORY_MIGRATIONS` defaults to the source-tree `migrations/` when present,
  otherwise the packaged `memory_mcp/migrations`.
- Migrations apply in numeric prefix order (`001_init.sql`), tracked in
  `meta.schema_version`. Add a new numbered file; never edit an applied one.
- `poi_rtree` is maintained by SQL triggers on `poi` INSERT/UPDATE/DELETE.
  Write POIs through the repo; never modify the R*Tree directly.
- "Last opened session" is `_repo.default_ctx_id`, in-process only. It is not
  persisted: a fresh process needs `open` or an explicit `session_id`.
- POI visibility is scope+owner based: `world` → owner `""`, `profile` →
  `p:<profile_id>`, `robot` → `r:<robot_id>`. Every read path must use
  `spatial.visibility_clause`.
- `remember_poi` is idempotent: merges same world/dim/scope/owner/kind and
  `item_uid` (or case-insensitive label when `item_uid` is empty) within
  `radius + 2`. Distance is horizontal Euclidean + 2× vertical.
- `item_uid` must match `scheme:name` (e.g. `ore:ingotIron`); raw ids like
  `265:0` are rejected, but `""` is allowed for unresolved items.
- POI ids are strings `poi:<int>`; `parse_poi_id` rejects other formats.
- TTL expiry is lazy: expired rows are filtered on read, not deleted.

## Repo hygiene

- `local/`, `data/`, `*.db`, `.venv/` are gitignored; never commit them.
- Docs: `README.md` (overview), `PLAN.md` (design), `docs/testing.md` (full
  manual test checklist), `agent/AGENTS.md` (runtime agent protocol).
