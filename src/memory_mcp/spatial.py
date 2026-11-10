from __future__ import annotations

import math
import sqlite3

from .util import now_ms


def visibility_clause(alias: str = "p") -> str:
    return (
        f"({alias}.scope = 'world' OR "
        f"({alias}.scope = 'profile' AND {alias}.owner = ?) OR "
        f"({alias}.scope = 'robot' AND {alias}.owner = ?))"
    )


def weighted_distance(ax: int, ay: int, az: int, bx: int, by: int, bz: int) -> float:
    """Path-cost proxy: horizontal distance dominates, vertical costs double."""
    return math.hypot(ax - bx, az - bz) + 2.0 * abs(ay - by)


def nearby_rows(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    profile_owner: str,
    robot_owner: str,
    dim: int,
    x: int,
    y: int,
    z: int,
    radius: int,
    kind: str | None = None,
    item_uid: str | None = None,
    limit: int = 500,
) -> list[tuple[sqlite3.Row, float]]:
    sql = f"""
        SELECT p.*
        FROM poi p
        JOIN poi_rtree r ON r.id = p.id
        WHERE p.world_id = ?
          AND r.dim_min <= ? AND r.dim_max >= ?
          AND r.x_min <= ? AND r.x_max >= ?
          AND r.y_min <= ? AND r.y_max >= ?
          AND r.z_min <= ? AND r.z_max >= ?
          AND (p.ttl_at IS NULL OR p.ttl_at > ?)
          AND {visibility_clause("p")}
    """
    params: list[object] = [
        world_id,
        dim,
        dim,
        x + radius,
        x - radius,
        y + radius,
        y - radius,
        z + radius,
        z - radius,
        now_ms(),
        profile_owner,
        robot_owner,
    ]
    if kind is not None:
        sql += " AND p.kind = ?"
        params.append(kind)
    if item_uid is not None:
        sql += " AND p.item_uid = ?"
        params.append(item_uid)
    sql += " LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    result: list[tuple[sqlite3.Row, float]] = []
    for row in rows:
        dist = weighted_distance(x, y, z, row["x"], row["y"], row["z"])
        if dist <= radius + row["radius"]:
            result.append((row, dist))
    return result


def search_rows(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    profile_owner: str,
    robot_owner: str,
    kind: str | None = None,
    item_uid: str | None = None,
    text_tokens: list[str] | None = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    sql = f"""
        SELECT p.*
        FROM poi p
        WHERE p.world_id = ?
          AND (p.ttl_at IS NULL OR p.ttl_at > ?)
          AND {visibility_clause("p")}
    """
    params: list[object] = [
        world_id,
        now_ms(),
        profile_owner,
        robot_owner,
    ]
    if kind is not None:
        sql += " AND p.kind = ?"
        params.append(kind)
    if item_uid is not None:
        sql += " AND p.item_uid = ?"
        params.append(item_uid)
    if text_tokens:
        clauses = []
        for token in text_tokens[:6]:
            clauses.append("(p.label LIKE ? OR p.tags LIKE ?)")
            pattern = f"%{token}%"
            params.extend([pattern, pattern])
        sql += " AND (" + " OR ".join(clauses) + ")"
    sql += " LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_visible_poi(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    profile_owner: str,
    robot_owner: str,
    poi_pk: int,
) -> sqlite3.Row | None:
    sql = f"""
        SELECT p.*
        FROM poi p
        WHERE p.id = ?
          AND p.world_id = ?
          AND {visibility_clause("p")}
    """
    return conn.execute(sql, (poi_pk, world_id, profile_owner, robot_owner)).fetchone()
