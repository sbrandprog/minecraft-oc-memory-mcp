from __future__ import annotations

import json
import re
import time

_POI_ID_RE = re.compile(r"^poi:(\d+)$")
_EPISODE_ID_RE = re.compile(r"^ep:(\d+)$")


def now_ms() -> int:
    return int(time.time() * 1000)


def format_poi_id(pk: int) -> str:
    return f"poi:{pk}"


def parse_poi_id(value: str) -> int:
    match = _POI_ID_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid poi id: {value!r} (expected 'poi:<int>')")
    return int(match.group(1))


def format_episode_id(pk: int) -> str:
    return f"ep:{pk}"


def parse_episode_id(value: str) -> int:
    match = _EPISODE_ID_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid episode id: {value!r} (expected 'ep:<int>')")
    return int(match.group(1))


def normalize_text(value: str) -> str:
    """Casefold and strip punctuation for Cyrillic-safe lexical search.

    SQLite's lower() and LIKE only fold ASCII unless built with ICU, so all
    searchable text is normalized in Python before it reaches the database.
    """
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"[^\w\s-]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def escape_like(value: str) -> str:
    """Escape LIKE wildcards for use with an ``ESCAPE '\\'`` clause."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def dump_list(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item and item.strip()]
    return json.dumps(cleaned, ensure_ascii=False)


def load_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [part for part in value.split() if part]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def dump_tags(tags: list[str]) -> str:
    cleaned = sorted({t.strip() for t in tags if t and t.strip()})
    return json.dumps(cleaned, ensure_ascii=False)


def load_tags(value: str | None) -> list[str]:
    return load_list(value)
