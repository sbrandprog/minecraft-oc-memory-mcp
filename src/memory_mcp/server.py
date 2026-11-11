from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from .config import load_settings
from .repo import MemoryRepo
from .schemas import (
    EpisodeRecord,
    OpenResult,
    PoiPatch,
    PoiRecord,
    Pose,
    PreferenceRecord,
    Vec3,
)

mcp = MCPServer(
    "memory-mcp",
    instructions=(
        "Persistent spatial and episodic memory for a Minecraft/OpenComputers "
        "robot agent. Call memory_open once per session; it returns session_id. "
        "Later tools accept session_id, but you may omit it: the server "
        "remembers the last opened session. Resolve item names through NEI "
        "first and store only its stable item_uid. Never pass raw runtime item "
        "ids such as '265:0'. When a task teaches a reusable lesson, save it "
        "with memory_remember_episode and read brief.lessons before planning."
    ),
)

_repo: MemoryRepo | None = None


def repo() -> MemoryRepo:
    global _repo
    if _repo is None:
        _repo = MemoryRepo(load_settings())
    return _repo


def _session(session_id: str | None) -> str:
    """Resolve an explicit session id or fall back to the last opened session."""
    if session_id:
        return session_id
    default = repo().default_ctx_id
    if not default:
        raise ValueError(
            "no active memory session; call memory_open first, or pass session_id"
        )
    return default


@mcp.tool(name="open")
def memory_open(world_id: str, profile_id: str, robot_id: str) -> OpenResult:
    """Open a memory session. Returns session_id (also available as ctx_id).

    world_id must identify a concrete world instance (server + seed + pack) and
    stay stable for that instance. profile_id is a player/team, robot_id is the
    acting robot. There is no sessionId field. Later memory tools accept
    session_id and can also omit it to use this session automatically.
    """
    result = repo().open_context(world_id, profile_id, robot_id)
    result["hint"] = (
        "Use session_id in later calls, or omit it to use the last opened session. "
        "The field is named session_id, not sessionId."
    )
    return OpenResult(**result)


@mcp.tool(name="brief")
def memory_brief(
    task: str | None = None,
    pose: Pose | None = None,
    item_uid: str | None = None,
    kind: str | None = None,
    radius: int = 64,
    budget_tokens: int = 1200,
    include_lessons: bool = True,
    lesson_budget_tokens: int | None = None,
    session_id: str | None = None,
) -> dict:
    """Get compact memory context for the current task.

    Always returns pinned core facts. If pose is given, adds nearby POIs sorted
    by proximity; task text, kind and item_uid add relevant POIs. When task is
    set, adds matching lessons from past episodes under lesson_budget_tokens.
    pose is {x, y, z, dim}; dim defaults to 0 (overworld), so pass it explicitly
    when the robot is in another dimension. item_uid must be a stable NEI uid,
    not a raw numeric id. session_id is optional after memory_open.
    """
    return repo().brief(
        _session(session_id),
        task=task,
        pose=pose,
        item_uid=item_uid,
        kind=kind,
        radius=radius,
        budget_tokens=budget_tokens,
        include_lessons=include_lessons,
        lesson_budget_tokens=lesson_budget_tokens,
    )


@mcp.tool(name="map")
def memory_map(
    dim: int,
    center: Vec3,
    radius: int = 64,
    format: Literal["ascii", "json"] = "ascii",
    session_id: str | None = None,
) -> dict:
    """Render known POIs around a point as an ASCII map or a JSON list.

    dim is the dimension id (0 = overworld). center is {x, y, z} and does not
    include dim. session_id is optional after memory_open.
    """
    return repo().map_pois(
        _session(session_id), dim=dim, center=center, radius=radius, fmt=format
    )


@mcp.tool(name="remember_poi")
def memory_remember_poi(record: PoiRecord, session_id: str | None = None) -> dict:
    """Create or refresh a point of interest.

    Idempotent: a matching POI within a small radius is merged and its
    last_seen_at is refreshed. Use scope='world' for shared world facts,
    'profile' for team facts, 'robot' for private facts. session_id is optional
    after memory_open.
    """
    return repo().remember_poi(_session(session_id), record)


@mcp.tool(name="set_preference")
def memory_set_preference(
    record: PreferenceRecord, session_id: str | None = None
) -> dict:
    """Pin a preference or durable fact (base, waypoint, player rule).

    session_id is optional after memory_open.
    """
    return repo().set_preference(_session(session_id), record)


@mcp.tool(name="remember_episode")
def memory_remember_episode(
    record: EpisodeRecord, session_id: str | None = None
) -> dict:
    """Save a reusable lesson from a completed task.

    Format the experience as: what was asked (goal), what got in the way
    (obstacles), and how to do it better next time (lesson). Set outcome to
    success, partial or failed. Identical goal+lesson pairs merge and bump
    occurrences. Use scope='world' to share the lesson with every robot,
    'profile' for the team, 'robot' for private experience. session_id is
    optional after memory_open.
    """
    return repo().remember_episode(_session(session_id), record)


@mcp.tool(name="recall_episodes")
def memory_recall_episodes(
    query: str | None = None,
    tags: list[str] | None = None,
    limit: int = 5,
    session_id: str | None = None,
) -> dict:
    """Find past episodes by task text and/or tags.

    Uses lexical matching over goal, obstacles, lesson and tags, then ranks by
    match quality, importance, recency and how often the lesson repeated.
    Without query, returns the most important/recent episodes. session_id is
    optional after memory_open.
    """
    return repo().recall_episodes(
        _session(session_id), query=query, tags=tags, limit=limit
    )


@mcp.tool(name="forget_episode")
def memory_forget_episode(
    episode_id: str | None = None,
    query: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Delete an episode by id (ep:<int>) or all visible episodes whose
    searchable text contains query.

    session_id is optional after memory_open.
    """
    return repo().forget_episode(
        _session(session_id), episode_id=episode_id, query=query
    )


@mcp.tool(name="update")
def memory_update(poi_id: str, patch: PoiPatch, session_id: str | None = None) -> dict:
    """Patch an existing POI (label, item_uid, tags, radius, confidence, ttl).

    session_id is optional after memory_open.
    """
    return repo().update_poi(_session(session_id), poi_id, patch)


@mcp.tool(name="forget")
def memory_forget(
    poi_id: str | None = None,
    query: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Delete a POI by id, or all visible POIs whose label matches query.

    session_id is optional after memory_open.
    """
    return repo().forget(_session(session_id), poi_id=poi_id, query=query)


@mcp.tool(name="stats")
def memory_stats(session_id: str | None = None) -> dict:
    """Return visible POI counts and database size for diagnostics.

    session_id is optional after memory_open.
    """
    return repo().stats(_session(session_id))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
