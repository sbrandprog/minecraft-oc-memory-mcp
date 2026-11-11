#!/usr/bin/env python3
"""End-to-end MCP smoke test: spawns memory-mcp over stdio and exercises tools.

Usage:
    uv run python scripts/mcp_smoke.py
    SMOKE_DB=/tmp/memory-smoke.db uv run python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "open",
    "brief",
    "map",
    "remember_poi",
    "set_preference",
    "remember_episode",
    "recall_episodes",
    "forget_episode",
    "update",
    "forget",
    "stats",
}


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


async def _call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if result.is_error:
        raise RuntimeError(f"{name} failed: {result.content[0].text}")
    return _payload(result)


async def main() -> None:
    db_path = Path(os.environ.get("SMOKE_DB", ROOT / "data" / "smoke.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    env = dict(os.environ)
    env["MEMORY_DB"] = str(db_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memory_mcp.server"],
        env=env,
        cwd=str(ROOT),
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = {tool.name for tool in (await session.list_tools()).tools}
        missing = EXPECTED_TOOLS - tools
        if missing:
            raise RuntimeError(f"missing tools: {sorted(missing)}")
        print(f"tools: {', '.join(sorted(tools))}")

        opened = await _call(
            session,
            "open",
            {
                "world_id": "smoke-world",
                "profile_id": "smoke-team",
                "robot_id": "smoke-robot",
            },
        )
        session_id = opened["session_id"]
        if session_id != opened["ctx_id"]:
            raise RuntimeError("session_id and ctx_id aliases differ")
        print(f"session: {session_id}")

        await _call(
            session,
            "set_preference",
            {
                "record": {
                    "key": "base",
                    "value": "overworld (10,64,10)",
                    "scope": "profile",
                },
            },
        )

        poi = await _call(
            session,
            "remember_poi",
            {
                "record": {
                    "kind": "chest",
                    "label": "iron chest",
                    "dim": 0,
                    "x": 10,
                    "y": 64,
                    "z": 10,
                    "item_uid": "ore:ingotIron",
                    "scope": "world",
                },
            },
        )
        print(f"poi: {poi['id']} merged={poi['merged']}")

        brief = await _call(
            session,
            "brief",
            {
                "task": "bring iron",
                "pose": {"dim": 0, "x": 10, "y": 64, "z": 12},
            },
        )
        if brief["core"][0]["key"] != "base":
            raise RuntimeError("core preference was not returned")
        if not brief["nearby"] or brief["nearby"][0]["item_uid"] != "ore:ingotIron":
            raise RuntimeError("nearby POI was not returned")
        print(
            f"brief: core={len(brief['core'])} nearby={len(brief['nearby'])} "
            f"tokens={brief['stats']['used_tokens']}"
        )

        episode = await _call(
            session,
            "remember_episode",
            {
                "record": {
                    "goal": "bring iron from the base chest",
                    "outcome": "partial",
                    "obstacles": ["the base chest was empty"],
                    "lesson": "check the second chest near the base first",
                    "tags": ["iron", "storage"],
                    "scope": "world",
                },
            },
        )
        print(f"episode: {episode['id']} merged={episode['merged']}")

        recalled = await _call(
            session, "recall_episodes", {"query": "bring iron", "limit": 3}
        )
        if recalled["count"] != 1:
            raise RuntimeError("episode was not recalled by query")
        if recalled["episodes"][0]["id"] != episode["id"]:
            raise RuntimeError("recall returned the wrong episode")
        print(f"recall: {recalled['count']} episode")

        brief = await _call(
            session,
            "brief",
            {
                "task": "bring iron",
                "pose": {"dim": 0, "x": 10, "y": 64, "z": 12},
            },
        )
        if not brief["lessons"]:
            raise RuntimeError("brief did not return a matching lesson")
        print(f"brief lessons: {len(brief['lessons'])}")

        mapped = await _call(
            session,
            "map",
            {
                "session_id": session_id,
                "dim": 0,
                "center": {"x": 10, "y": 64, "z": 10},
                "radius": 16,
            },
        )
        if "legend" not in mapped["ascii"]:
            raise RuntimeError("ascii map has no legend")
        print(f"map: {mapped['count']} poi")

        rejected = await session.call_tool(
            "remember_poi",
            {
                "record": {
                    "kind": "ore",
                    "label": "raw id",
                    "dim": 0,
                    "x": 1,
                    "y": 64,
                    "z": 1,
                    "item_uid": "265:0",
                },
            },
        )
        if not rejected.is_error:
            raise RuntimeError("raw item id was not rejected")
        print("validation: raw item id rejected")

        forgotten = await _call(
            session, "forget", {"session_id": session_id, "poi_id": poi["id"]}
        )
        if forgotten["deleted"] != 1:
            raise RuntimeError("poi was not deleted")
        print("forget: 1 poi deleted")

        forgotten_episode = await _call(
            session,
            "forget_episode",
            {"session_id": session_id, "episode_id": episode["id"]},
        )
        if forgotten_episode["deleted"] != 1:
            raise RuntimeError("episode was not deleted")
        print("forget: 1 episode deleted")

        stats = await _call(session, "stats", {})
        print(
            f"stats: pois={stats['poi_total']} "
            f"episodes={stats['episode_total']} "
            f"db={stats['db_bytes']} bytes"
        )

    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
