from __future__ import annotations

import pytest
from pydantic import ValidationError

from memory_mcp.repo import MemoryRepo
from memory_mcp.schemas import EpisodeRecord


def _episode(**overrides) -> EpisodeRecord:
    data = {
        "goal": "принести железо из сундука",
        "outcome": "partial",
        "obstacles": [
            "сундук у базы был пуст",
            "путь через лаву",
        ],
        "lesson": (
            "сначала проверить сундук у базы, затем брать запас "
            "из второго сундука"
        ),
        "tags": ["iron", "storage"],
        "importance": 0.8,
    }
    data.update(overrides)
    return EpisodeRecord(**data)


def test_remember_and_recall_episode(repo: MemoryRepo, ctx: str) -> None:
    saved = repo.remember_episode(ctx, _episode())
    assert saved["merged"] is False
    assert saved["id"].startswith("ep:")

    found = repo.recall_episodes(
        ctx, query="принеси железо из сундука"
    )
    assert found["count"] == 1
    episode = found["episodes"][0]
    assert episode["id"] == saved["id"]
    assert episode["lesson"].startswith("сначала проверить")
    assert "принеси" in episode["matched"]
    assert episode["score"] > 0

    missing = repo.recall_episodes(ctx, query="построить плавильню")
    assert missing == {"episodes": [], "count": 0}


def test_episode_merge_bumps_occurrences(
    repo: MemoryRepo, ctx: str
) -> None:
    first = repo.remember_episode(ctx, _episode())
    second = repo.remember_episode(
        ctx,
        _episode(
            goal="Принести  железо из сундука",
            obstacles=["на обратном пути сломался мост"],
            tags=["Iron", "bridge"],
            outcome="failed",
        ),
    )

    assert second["merged"] is True
    assert first["id"] == second["id"]

    found = repo.recall_episodes(ctx, query="железо сундук")
    episode = found["episodes"][0]
    assert episode["occurrences"] == 2
    assert "на обратном пути сломался мост" in episode["obstacles"]
    assert "bridge" in episode["tags"]
    assert episode["outcome"] == "failed"


def test_episode_visibility(repo: MemoryRepo) -> None:
    c1 = repo.open_context("w1", "team1", "r1")["ctx_id"]
    c2 = repo.open_context("w1", "team1", "r2")["ctx_id"]
    c3 = repo.open_context("w1", "team2", "r3")["ctx_id"]
    c4 = repo.open_context("w2", "team1", "r1")["ctx_id"]

    world = repo.remember_episode(c1, _episode(scope="world"))
    profile = repo.remember_episode(
        c1, _episode(scope="profile", goal="profile lesson")
    )
    robot = repo.remember_episode(
        c1, _episode(scope="robot", goal="robot lesson")
    )

    ids_r2 = {
        item["id"]
        for item in repo.recall_episodes(c2, limit=10)["episodes"]
    }
    assert world["id"] in ids_r2
    assert profile["id"] in ids_r2
    assert robot["id"] not in ids_r2

    ids_c3 = {
        item["id"]
        for item in repo.recall_episodes(c3, limit=10)["episodes"]
    }
    assert world["id"] in ids_c3
    assert profile["id"] not in ids_c3
    assert robot["id"] not in ids_c3

    assert repo.recall_episodes(c4, limit=10)["episodes"] == []


def test_episode_tag_filter(repo: MemoryRepo, ctx: str) -> None:
    storage = repo.remember_episode(ctx, _episode(tags=["storage"]))
    repo.remember_episode(
        ctx,
        _episode(
            goal="собрать урожай",
            lesson="сначала проверить рост культур",
            tags=["farming"],
        ),
    )

    found = repo.recall_episodes(ctx, tags=["storage"])
    assert found["count"] == 1
    assert found["episodes"][0]["id"] == storage["id"]


def test_brief_returns_relevant_lessons(
    repo: MemoryRepo, ctx: str
) -> None:
    repo.remember_episode(ctx, _episode())

    brief = repo.brief(ctx, task="принеси железо")
    assert len(brief["lessons"]) == 1
    assert brief["lessons"][0]["id"].startswith("ep:")
    assert brief["stats"]["lessons_returned"] == 1
    assert brief["stats"]["lesson_tokens"] > 0

    unrelated = repo.brief(ctx, task="построй дом")
    assert unrelated["lessons"] == []

    hidden = repo.brief(ctx, task="принеси железо", include_lessons=False)
    assert hidden["lessons"] == []


def test_forget_episode(repo: MemoryRepo, ctx: str) -> None:
    saved = repo.remember_episode(ctx, _episode())
    assert repo.forget_episode(ctx, episode_id=saved["id"])["deleted"] == 1
    assert repo.recall_episodes(ctx, query="железо")["count"] == 0

    repo.remember_episode(ctx, _episode())
    repo.remember_episode(ctx, _episode(goal="починить мост"))
    assert repo.forget_episode(ctx, query="железо")["deleted"] == 1
    assert repo.recall_episodes(ctx, limit=10)["count"] == 1


def test_forget_episode_rejects_unsearchable_query(
    repo: MemoryRepo, ctx: str
) -> None:
    repo.remember_episode(ctx, _episode())
    with pytest.raises(ValueError, match="searchable"):
        repo.forget_episode(ctx, query="!!!")
    assert repo.recall_episodes(ctx, limit=10)["count"] == 1


def test_recall_matches_shorter_stored_word(
    repo: MemoryRepo, ctx: str
) -> None:
    saved = repo.remember_episode(
        ctx,
        _episode(goal="build a smelter", lesson="gather clay first", tags=[]),
    )
    found = repo.recall_episodes(ctx, query="building")
    assert found["count"] == 1
    assert found["episodes"][0]["id"] == saved["id"]


def test_recall_tags_work_with_unusable_query(
    repo: MemoryRepo, ctx: str
) -> None:
    saved = repo.remember_episode(ctx, _episode(tags=["storage"]))
    found = repo.recall_episodes(ctx, query="go", tags=["storage"])
    assert found["count"] == 1
    assert found["episodes"][0]["id"] == saved["id"]


def test_brief_blank_task_returns_no_lessons(
    repo: MemoryRepo, ctx: str
) -> None:
    repo.remember_episode(ctx, _episode())
    assert repo.brief(ctx, task="   ")["lessons"] == []
    assert repo.brief(ctx, task="железо")["lessons"]


def test_merge_caps_obstacles_and_tags(repo: MemoryRepo, ctx: str) -> None:
    first = repo.remember_episode(
        ctx,
        _episode(
            obstacles=[f"obstacle {index}" for index in range(10)],
            tags=[f"tag{index}" for index in range(32)],
        ),
    )
    second = repo.remember_episode(
        ctx, _episode(obstacles=["one more"], tags=["one-more"])
    )

    assert second["merged"] is True
    assert second["id"] == first["id"]
    found = repo.recall_episodes(ctx, limit=10)["episodes"][0]
    assert len(found["obstacles"]) == 10
    assert len(found["tags"]) == 32


def test_episode_merge_key_is_unique(repo: MemoryRepo) -> None:
    indexes = {
        row["name"]
        for row in repo.conn.execute("PRAGMA index_list('episode')")
    }
    assert "idx_episode_merge" in indexes


def test_episode_merge_handles_insert_conflict(
    repo: MemoryRepo, ctx: str, monkeypatch
) -> None:
    saved = repo.remember_episode(ctx, _episode())
    real_find = repo._find_episode
    calls = {"count": 0}

    def racing_find(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            # Simulate another writer inserting the row after our lookup.
            return None
        return real_find(**kwargs)

    monkeypatch.setattr(repo, "_find_episode", racing_find)

    merged = repo.remember_episode(ctx, _episode())
    assert merged == {"id": saved["id"], "merged": True}
    found = repo.recall_episodes(ctx, query="железо")
    assert found["episodes"][0]["occurrences"] == 2


def test_recall_rejects_unsearchable_query(
    repo: MemoryRepo, ctx: str
) -> None:
    repo.remember_episode(ctx, _episode())
    assert repo.recall_episodes(ctx, query="!!!") == {
        "episodes": [],
        "count": 0,
    }
    # Whitespace-only input is treated like an omitted query.
    assert repo.recall_episodes(ctx, query="   ")["count"] == 1


def test_forget_episode_query_treats_wildcards_literally(
    repo: MemoryRepo, ctx: str
) -> None:
    repo.remember_episode(ctx, _episode(goal="find iron chest"))
    repo.remember_episode(
        ctx,
        _episode(
            goal="find ironXchest",
            lesson="another lesson entirely",
            tags=[],
        ),
    )

    assert repo.forget_episode(ctx, query="iron_chest")["deleted"] == 0
    assert repo.recall_episodes(ctx, query="iron chest")["count"] == 2
    assert repo.forget_episode(ctx, query="iron chest")["deleted"] == 1


def test_episode_tag_length_validation() -> None:
    with pytest.raises(ValidationError):
        EpisodeRecord(goal="задача", lesson="урок", tags=["x" * 129])


def test_episode_validation() -> None:
    with pytest.raises(ValidationError):
        EpisodeRecord(goal="задача", lesson="   ")

    with pytest.raises(ValidationError):
        EpisodeRecord(
            goal="задача",
            lesson="урок",
            obstacles=[f"problem {index}" for index in range(11)],
        )


def test_stats_include_episodes(repo: MemoryRepo, ctx: str) -> None:
    repo.remember_episode(ctx, _episode())
    repo.remember_episode(ctx, _episode(goal="другая задача", outcome="success"))

    stats = repo.stats(ctx)
    assert stats["episode_total"] == 2
    assert stats["episodes_by_outcome"] == {"partial": 1, "success": 1}
