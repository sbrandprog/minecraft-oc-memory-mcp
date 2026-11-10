from __future__ import annotations

import json
import re
import time

_POI_ID_RE = re.compile(r"^poi:(\d+)$")


def now_ms() -> int:
    return int(time.time() * 1000)


def format_poi_id(pk: int) -> str:
    return f"poi:{pk}"


def parse_poi_id(value: str) -> int:
    match = _POI_ID_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid poi id: {value!r} (expected 'poi:<int>')")
    return int(match.group(1))


def dump_tags(tags: list[str]) -> str:
    cleaned = sorted({t.strip() for t in tags if t and t.strip()})
    return json.dumps(cleaned, ensure_ascii=False)


def load_tags(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [part for part in value.split() if part]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []
