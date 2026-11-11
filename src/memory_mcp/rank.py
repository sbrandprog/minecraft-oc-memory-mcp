from __future__ import annotations

import json
import math

SOURCE_WEIGHT = {"observed": 1.0, "told": 0.7, "inferred": 0.4}
RECENCY_HALF_LIFE_MS = 3 * 24 * 60 * 60 * 1000
EPISODE_RECENCY_HALF_LIFE_MS = 30 * 24 * 60 * 60 * 1000
EPISODE_FIELD_WEIGHTS = {
    "goal": 1.2,
    "lesson": 1.0,
    "tags": 0.9,
    "obstacles": 0.7,
}
# Minimum leading characters for a fuzzy token match. The SQL prefilter in
# MemoryRepo._search_prefixes relies on this same prefix length.
EPISODE_MIN_PREFIX = 4


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


def _common_prefix_length(first: str, second: str) -> int:
    length = 0
    for left, right in zip(first, second):
        if left != right:
            break
        length += 1
    return length


def _token_matches(query_token: str, doc_tokens: set[str]) -> bool:
    if query_token in doc_tokens:
        return True
    if len(query_token) < EPISODE_MIN_PREFIX:
        return False
    for doc_token in doc_tokens:
        if len(doc_token) < EPISODE_MIN_PREFIX:
            continue
        shared = _common_prefix_length(query_token, doc_token)
        threshold = max(
            EPISODE_MIN_PREFIX, min(len(query_token), len(doc_token)) - 2
        )
        if shared >= threshold:
            return True
    return False


def episode_text_score(
    query_tokens: list[str],
    field_tokens: dict[str, set[str]],
    *,
    full_query: str = "",
    search_text: str = "",
) -> tuple[float, list[str]]:
    """Score lexical overlap between a task/query and one episode."""
    if not query_tokens:
        return 0.0, []
    max_weight = max(EPISODE_FIELD_WEIGHTS.values())
    matched: list[str] = []
    total = 0.0
    for token in query_tokens:
        best = 0.0
        for field, weight in EPISODE_FIELD_WEIGHTS.items():
            if weight > best and _token_matches(token, field_tokens.get(field, set())):
                best = weight
        if best:
            matched.append(token)
            total += best
    score = total / (len(query_tokens) * max_weight)
    if (
        full_query
        and len(full_query) >= 8
        and full_query in search_text
    ):
        score += 0.10
    return min(score, 1.0), matched


def score_episode(
    row,
    *,
    now_ms: int,
    text_score: float = 0.0,
    tag_score: float = 0.0,
    query_used: bool = True,
) -> float:
    age = max(0, now_ms - int(row["last_seen_at"]))
    recency = 0.5 ** (age / EPISODE_RECENCY_HALF_LIFE_MS)
    importance = float(row["importance"])
    occurrence = min(
        1.0, math.log1p(int(row["occurrences"])) / math.log1p(5)
    )
    if not query_used:
        return 0.45 * importance + 0.35 * recency + 0.20 * occurrence
    return (
        0.50 * text_score
        + 0.15 * tag_score
        + 0.15 * importance
        + 0.12 * recency
        + 0.08 * occurrence
    )


def apply_episode_budget(
    items: list[dict],
    budget_tokens: int,
) -> tuple[list[dict], int]:
    selected: list[dict] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item)
        if selected and used + cost > budget_tokens:
            break
        selected.append(item)
        used += cost
    return selected, used
