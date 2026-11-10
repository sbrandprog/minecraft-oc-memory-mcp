from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from .config import load_settings
from .repo import MemoryRepo
from .schemas import PoiPatch, PoiRecord, Pose, PreferenceRecord, Vec3

mcp = MCPServer(
    "memory-mcp",
    instructions=(
        "Persistent spatial memory for a Minecraft/OpenComputers robot agent. "
        "Call memory_open once per session, then memory_brief at task start. "
        "Resolve item names through NEI first and store only its stable item_uid. "
        "Never pass raw runtime item ids such as '265:0'."
    ),
)

_repo: MemoryRepo | None = None


def repo() -> MemoryRepo:
    global _repo
    if _repo is None:
        _repo = MemoryRepo(load_settings())
    return _repo


@mcp.tool(name="open")
def memory_open(world_id: str, profile_id: str, robot_id: str) -> dict:
    """Open a memory session and return a ctx_id.

    world_id must identify a concrete world instance (server + seed + pack) and
    stay stable for that instance. profile_id is a player/team, robot_id is the
    acting robot. All following calls must use the returned ctx_id.
    """
    return repo().open_context(world_id, profile_id, robot_id)


@mcp.tool(name="brief")
def memory_brief(
    ctx_id: str,
    task: str | None = None,
    pose: Pose | None = None,
    item_uid: str | None = None,
    kind: str | None = None,
    radius: int = 64,
    budget_tokens: int = 1200,
) -> dict:
    """Get compact memory context for the current task.

    Always returns pinned core facts. If pose is given, adds nearby POIs sorted
    by proximity; task text, kind and item_uid add relevant POIs. item_uid must
    be a stable NEI uid, not a raw numeric id.
    """
    return repo().brief(
        ctx_id,
        task=task,
        pose=pose,
        item_uid=item_uid,
        kind=kind,
        radius=radius,
        budget_tokens=budget_tokens,
    )


@mcp.tool(name="map")
def memory_map(
    ctx_id: str,
    dim: int,
    center: Vec3,
    radius: int = 64,
    format: Literal["ascii", "json"] = "ascii",
) -> dict:
    """Render known POIs around a point as an ASCII map or a JSON list."""
    return repo().map_pois(ctx_id, dim=dim, center=center, radius=radius, fmt=format)


@mcp.tool(name="remember_poi")
def memory_remember_poi(ctx_id: str, record: PoiRecord) -> dict:
    """Create or refresh a point of interest.

    Idempotent: a matching POI within a small radius is merged and its
    last_seen_at is refreshed. Use scope='world' for shared world facts,
    'profile' for team facts, 'robot' for private facts.
    """
    return repo().remember_poi(ctx_id, record)


@mcp.tool(name="set_preference")
def memory_set_preference(ctx_id: str, record: PreferenceRecord) -> dict:
    """Pin a preference or durable fact (base, waypoint, player rule)."""
    return repo().set_preference(ctx_id, record)


@mcp.tool(name="update")
def memory_update(ctx_id: str, poi_id: str, patch: PoiPatch) -> dict:
    """Patch an existing POI (label, item_uid, tags, radius, confidence, ttl)."""
    return repo().update_poi(ctx_id, poi_id, patch)


@mcp.tool(name="forget")
def memory_forget(
    ctx_id: str, poi_id: str | None = None, query: str | None = None
) -> dict:
    """Delete a POI by id, or all visible POIs whose label matches query."""
    return repo().forget(ctx_id, poi_id=poi_id, query=query)


@mcp.tool(name="stats")
def memory_stats(ctx_id: str) -> dict:
    """Return visible POI counts and database size for diagnostics."""
    return repo().stats(ctx_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
