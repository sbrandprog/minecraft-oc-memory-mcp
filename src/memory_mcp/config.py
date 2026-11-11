from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    migrations_dir: Path
    context_ttl_s: int = 12 * 60 * 60
    default_radius: int = 64
    default_budget_tokens: int = 1200
    max_results: int = 200
    lesson_budget_tokens: int = 300
    max_lessons: int = 3


def _default_migrations_dir() -> Path:
    source_tree = _REPO_ROOT / "migrations"
    if source_tree.is_dir():
        return source_tree
    return Path(__file__).resolve().parent / "migrations"


def load_settings() -> Settings:
    db_path = Path(os.environ.get("MEMORY_DB", str(Path.cwd() / "data" / "memory.db")))
    migrations_dir = Path(
        os.environ.get("MEMORY_MIGRATIONS", str(_default_migrations_dir()))
    )
    return Settings(
        db_path=db_path,
        migrations_dir=migrations_dir,
        context_ttl_s=int(os.environ.get("MEMORY_CONTEXT_TTL_S", "43200")),
        default_radius=int(os.environ.get("MEMORY_DEFAULT_RADIUS", "64")),
        default_budget_tokens=int(
            os.environ.get("MEMORY_DEFAULT_BUDGET_TOKENS", "1200")
        ),
        max_results=int(os.environ.get("MEMORY_MAX_RESULTS", "200")),
        lesson_budget_tokens=int(
            os.environ.get("MEMORY_LESSON_BUDGET_TOKENS", "300")
        ),
        max_lessons=int(os.environ.get("MEMORY_MAX_LESSONS", "3")),
    )
