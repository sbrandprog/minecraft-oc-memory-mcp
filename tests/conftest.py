from __future__ import annotations

from pathlib import Path

import pytest

from memory_mcp.config import Settings
from memory_mcp.repo import MemoryRepo

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(tmp_path: Path) -> MemoryRepo:
    settings = Settings(
        db_path=tmp_path / "memory.db",
        migrations_dir=REPO_ROOT / "migrations",
    )
    instance = MemoryRepo(settings)
    yield instance
    instance.close()


@pytest.fixture()
def ctx(repo: MemoryRepo) -> str:
    return repo.open_context("survival-demo", "team1", "robot-1")["ctx_id"]
