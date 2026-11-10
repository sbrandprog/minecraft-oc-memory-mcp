from __future__ import annotations

import json

SOURCE_WEIGHT = {"observed": 1.0, "told": 0.7, "inferred": 0.4}
RECENCY_HALF_LIFE_MS = 3 * 24 * 60 * 60 * 1000


def score_row(
    row,
    *,
    now_ms: int,
    dist: float | None,
    item_exact: bool = False,
) -> float:
    age = max(0, now_ms - int(row["last_seen_at"]))
    recency = 0.5 ** (age / RECENCY_HALF_LIFE_MS)
    source = SOURCE_WEIGHT.get(row["source"], 0.5)
    if dist is None:
        distance = 0.5
    else:
        distance = 1.0 / (1.0 + dist / 32.0)
    score = (
        0.35 * source
        + 0.25 * float(row["confidence"])
        + 0.25 * recency
        + 0.15 * distance
    )
    if item_exact:
        score += 0.10
    return score


def estimate_tokens(value: object) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=False, default=str)) // 4)


def apply_budget(
    core: list[dict],
    items: list[dict],
    budget_tokens: int,
) -> tuple[list[dict], int]:
    """Keep core unconditionally; fill the rest with the highest-ranked items."""
    selected: list[dict] = []
    used = estimate_tokens(core)
    for item in items:
        cost = estimate_tokens(item)
        if selected and used + cost > budget_tokens:
            break
        selected.append(item)
        used += cost
    return selected, used
