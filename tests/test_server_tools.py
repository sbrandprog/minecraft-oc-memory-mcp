from __future__ import annotations

import pytest

from memory_mcp import server
from memory_mcp.repo import MemoryRepo
from memory_mcp.schemas import PoiRecord, Pose, PreferenceRecord


def test_open_sets_implicit_session(repo: MemoryRepo, monkeypatch) -> None:
    monkeypatch.setattr(server, "_repo", repo)

    opened = server.memory_open("smoke-world", "team1", "robot-1")
    assert opened.session_id == opened.ctx_id
    assert not hasattr(opened, "sessionId")
    assert "session_id" in opened.hint

    server.memory_set_preference(
        PreferenceRecord(key="base", value="overworld (10,64,10)", scope="profile")
    )
    brief = server.memory_brief()
    assert brief["core"][0]["key"] == "base"

    saved = server.memory_remember_poi(
        PoiRecord(
            kind="chest",
            label="iron",
            dim=0,
            x=10,
            y=64,
            z=10,
            item_uid="ore:ingotIron",
        )
    )
    assert saved["id"].startswith("poi:")

    brief = server.memory_brief(
        session_id=opened.session_id, pose=Pose(dim=0, x=10, y=64, z=12)
    )
    assert brief["nearby"][0]["item_uid"] == "ore:ingotIron"


def test_brief_pose_dim_defaults_to_overworld(
    repo: MemoryRepo, monkeypatch
) -> None:
    monkeypatch.setattr(server, "_repo", repo)
    opened = server.memory_open("smoke-world", "team1", "robot-1")
    server.memory_remember_poi(
        PoiRecord(
            kind="chest",
            label="iron",
            dim=0,
            x=10,
            y=64,
            z=10,
            item_uid="ore:ingotIron",
        )
    )

    brief = server.memory_brief(
        session_id=opened.session_id, pose=Pose(x=10, y=64, z=12)
    )

    assert brief["nearby"][0]["dim"] == 0
    assert brief["nearby"][0]["dist"] == 2


def test_tools_require_open_without_implicit_session(
    repo: MemoryRepo, monkeypatch
) -> None:
    monkeypatch.setattr(server, "_repo", repo)
    with pytest.raises(ValueError, match="no active memory session"):
        server.memory_brief()
