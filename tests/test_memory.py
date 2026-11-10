from __future__ import annotations

from memory_mcp.repo import MemoryRepo
from memory_mcp.schemas import PoiPatch, PoiRecord, Pose, PreferenceRecord


def _poi(**overrides) -> PoiRecord:
    data = {
        "kind": "chest",
        "label": "iron chest",
        "dim": 0,
        "x": 100,
        "y": 64,
        "z": -200,
        "item_uid": "ore:ingotIron",
    }
    data.update(overrides)
    return PoiRecord(**data)


def test_open_context_and_core_preference(repo: MemoryRepo, ctx: str) -> None:
    repo.set_preference(
        ctx,
        PreferenceRecord(key="base", value="overworld (100,64,-200)", scope="profile"),
    )
    brief = repo.brief(ctx)
    assert brief["core"]
    assert brief["core"][0]["key"] == "base"
    assert brief["core"][0]["value"] == "overworld (100,64,-200)"


def test_remember_and_brief_nearby(repo: MemoryRepo, ctx: str) -> None:
    result = repo.remember_poi(ctx, _poi())
    assert result["merged"] is False
    assert result["id"].startswith("poi:")

    brief = repo.brief(ctx, pose=Pose(dim=0, x=100, y=64, z=-190), radius=64)
    assert len(brief["nearby"]) == 1
    assert brief["nearby"][0]["item_uid"] == "ore:ingotIron"
    assert brief["nearby"][0]["dist"] == 10


def test_remember_is_idempotent(repo: MemoryRepo, ctx: str) -> None:
    first = repo.remember_poi(ctx, _poi())
    second = repo.remember_poi(ctx, _poi(label="iron chest full"))
    assert second["merged"] is True
    assert first["id"] == second["id"]

    brief = repo.brief(ctx, pose=Pose(dim=0, x=100, y=64, z=-200))
    assert len(brief["nearby"]) == 1
    assert brief["nearby"][0]["label"] == "iron chest full"


def test_ttl_expires_poi(repo: MemoryRepo, ctx: str) -> None:
    repo.remember_poi(ctx, _poi(ttl_s=0))
    brief = repo.brief(ctx, pose=Pose(dim=0, x=100, y=64, z=-200))
    assert brief["nearby"] == []


def test_update_poi(repo: MemoryRepo, ctx: str) -> None:
    poi_id = repo.remember_poi(ctx, _poi())["id"]
    updated = repo.update_poi(
        ctx,
        poi_id,
        PoiPatch(label="iron storage", tags=["storage"], confidence=0.8),
    )
    assert updated["label"] == "iron storage"
    assert updated["tags"] == ["storage"]
    assert updated["confidence"] == 0.8


def test_forget_by_id_and_query(repo: MemoryRepo, ctx: str) -> None:
    poi_id = repo.remember_poi(ctx, _poi())["id"]
    assert repo.forget(ctx, poi_id=poi_id)["deleted"] == 1
    assert repo.brief(ctx)["nearby"] == []

    repo.remember_poi(ctx, _poi())
    repo.remember_poi(ctx, _poi(z=-210, label="coal chest", item_uid="ore:coal"))
    assert repo.forget(ctx, query="chest")["deleted"] == 2


def test_visibility_between_robots_and_profiles(repo: MemoryRepo) -> None:
    c1 = repo.open_context("w1", "team1", "r1")["ctx_id"]
    c2 = repo.open_context("w1", "team1", "r2")["ctx_id"]
    c3 = repo.open_context("w1", "team2", "r1")["ctx_id"]

    robot_private = repo.remember_poi(c1, _poi(scope="robot", label="private stash"))
    profile_shared = repo.remember_poi(c1, _poi(scope="profile", z=-205))
    world_shared = repo.remember_poi(c1, _poi(scope="world", z=-215))

    pose = Pose(dim=0, x=100, y=64, z=-200)
    r2 = repo.brief(c2, pose=pose, radius=64)
    ids_r2 = {item["id"] for item in r2["nearby"]}
    assert robot_private["id"] not in ids_r2
    assert profile_shared["id"] in ids_r2
    assert world_shared["id"] in ids_r2

    other_profile = repo.brief(c3, pose=pose, radius=64)
    ids_c3 = {item["id"] for item in other_profile["nearby"]}
    assert profile_shared["id"] not in ids_c3
    assert world_shared["id"] in ids_c3

    # A different world must not see anything from w1.
    c4 = repo.open_context("w2", "team1", "r1")["ctx_id"]
    assert repo.brief(c4, pose=pose, radius=64)["nearby"] == []


def test_map_ascii(repo: MemoryRepo, ctx: str) -> None:
    repo.remember_poi(ctx, _poi())
    result = repo.map_pois(
        ctx,
        dim=0,
        center=Pose(dim=0, x=100, y=64, z=-200),
        radius=32,
    )
    assert result["count"] == 1
    assert "legend" in result["ascii"]
    assert "poi:" in result["ascii"]
