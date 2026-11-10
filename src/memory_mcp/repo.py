from __future__ import annotations

import re
import secrets
import sqlite3

from .config import Settings
from .db import connect, migrate
from .rank import apply_budget, score_row
from .render import render_ascii
from .schemas import PoiPatch, PoiRecord, Pose, PreferenceRecord, Vec3
from .spatial import (
    get_visible_poi,
    nearby_rows,
    search_rows,
    visibility_clause,
    weighted_distance,
)
from .util import (
    dump_tags,
    format_poi_id,
    load_tags,
    now_ms,
    parse_poi_id,
)
from .validate import (
    validate_item_uid,
    validate_profile_id,
    validate_robot_id,
    validate_world_id,
)

MERGE_TOLERANCE = 2
_DEFAULT_LIMIT = 200
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "into",
    "from",
    "мне",
    "надо",
    "нужно",
    "это",
    "как",
    "где",
    "чем",
    "для",
    "или",
}


class MemoryRepo:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.conn = connect(settings.db_path)
        self.default_ctx_id: str | None = None
        migrate(self.conn, settings.migrations_dir)

    # ------------------------------------------------------------------ context

    def open_context(self, world_id: str, profile_id: str, robot_id: str) -> dict:
        world_id = validate_world_id(world_id)
        profile_id = validate_profile_id(profile_id)
        robot_id = validate_robot_id(robot_id)
        now = now_ms()
        self.conn.execute(
            """
            INSERT INTO world(world_id, created_at, last_seen_at) VALUES (?, ?, ?)
            ON CONFLICT(world_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (world_id, now, now),
        )
        ctx_id = "ctx_" + secrets.token_hex(16)
        self.conn.execute(
            """
            INSERT INTO context(
              ctx_id, world_id, profile_id, robot_id,
              created_at, last_seen_at, expires_at, revoked
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                ctx_id,
                world_id,
                profile_id,
                robot_id,
                now,
                now,
                now + self.settings.context_ttl_s * 1000,
            ),
        )
        self.conn.commit()
        self.default_ctx_id = ctx_id
        return {
            "session_id": ctx_id,
            "ctx_id": ctx_id,
            "world_id": world_id,
            "profile_id": profile_id,
            "robot_id": robot_id,
        }

    def require_context(self, ctx_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM context WHERE ctx_id = ?", (ctx_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown session_id; call memory_open first")
        if row["revoked"]:
            raise ValueError("session_id has been revoked")
        now = now_ms()
        if row["expires_at"] <= now:
            raise ValueError("session_id has expired; call memory_open again")
        self.conn.execute(
            "UPDATE context SET last_seen_at = ? WHERE ctx_id = ?", (now, ctx_id)
        )
        self.conn.commit()
        return row

    def _owners(self, ctx: sqlite3.Row) -> tuple[str, str]:
        return f"p:{ctx['profile_id']}", f"r:{ctx['robot_id']}"

    @staticmethod
    def _owner_for(scope: str, ctx: sqlite3.Row) -> str:
        if scope == "world":
            return ""
        if scope == "profile":
            return f"p:{ctx['profile_id']}"
        if scope == "robot":
            return f"r:{ctx['robot_id']}"
        raise ValueError(f"invalid scope: {scope!r}")

    # ---------------------------------------------------------------- rendering

    @staticmethod
    def _row_to_poi(row: sqlite3.Row, dist: float | None = None) -> dict:
        result = {
            "id": format_poi_id(row["id"]),
            "kind": row["kind"],
            "label": row["label"],
            "dim": row["dim"],
            "x": row["x"],
            "y": row["y"],
            "z": row["z"],
            "radius": row["radius"],
            "item_uid": row["item_uid"],
            "tags": load_tags(row["tags"]),
            "scope": row["scope"],
            "source": row["source"],
            "confidence": row["confidence"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "ttl_at": row["ttl_at"],
        }
        if dist is not None:
            result["dist"] = round(dist, 2)
        return result

    # -------------------------------------------------------------------- write

    def _find_merge_candidate(
        self, ctx: sqlite3.Row, record: PoiRecord, owner: str
    ) -> sqlite3.Row | None:
        radius = record.radius + MERGE_TOLERANCE
        sql = """
            SELECT p.*
            FROM poi p
            JOIN poi_rtree r ON r.id = p.id
            WHERE p.world_id = ?
              AND p.dim = ?
              AND p.scope = ?
              AND p.owner = ?
              AND p.kind = ?
              AND r.dim_min <= ? AND r.dim_max >= ?
              AND r.x_min <= ? AND r.x_max >= ?
              AND r.y_min <= ? AND r.y_max >= ?
              AND r.z_min <= ? AND r.z_max >= ?
              AND (p.ttl_at IS NULL OR p.ttl_at > ?)
        """
        params: list[object] = [
            ctx["world_id"],
            record.dim,
            record.scope,
            owner,
            record.kind,
            record.dim,
            record.dim,
            record.x + radius,
            record.x - radius,
            record.y + radius,
            record.y - radius,
            record.z + radius,
            record.z - radius,
            now_ms(),
        ]
        if record.item_uid:
            sql += " AND p.item_uid = ?"
            params.append(record.item_uid)
        else:
            sql += " AND lower(p.label) = ?"
            params.append(record.label.lower())
        sql += " LIMIT 50"

        for row in self.conn.execute(sql, params).fetchall():
            dist = weighted_distance(
                record.x, record.y, record.z, row["x"], row["y"], row["z"]
            )
            if dist <= radius + row["radius"]:
                return row
        return None

    def remember_poi(self, ctx_id: str, record: PoiRecord) -> dict:
        ctx = self.require_context(ctx_id)
        owner = self._owner_for(record.scope, ctx)
        now = now_ms()
        ttl_at = now + record.ttl_s * 1000 if record.ttl_s is not None else None
        tags = dump_tags(record.tags)

        candidate = self._find_merge_candidate(ctx, record, owner)
        if candidate is not None:
            merged_tags = dump_tags(load_tags(candidate["tags"]) + record.tags)
            self.conn.execute(
                """
                UPDATE poi
                SET label = ?,
                    tags = ?,
                    confidence = MAX(confidence, ?),
                    radius = MAX(radius, ?),
                    last_seen_at = ?,
                    ttl_at = COALESCE(?, ttl_at)
                WHERE id = ?
                """,
                (
                    record.label,
                    merged_tags,
                    record.confidence,
                    record.radius,
                    now,
                    ttl_at,
                    candidate["id"],
                ),
            )
            self.conn.commit()
            return {"id": format_poi_id(candidate["id"]), "merged": True}

        try:
            cur = self.conn.execute(
                """
                INSERT INTO poi(
                  world_id, scope, owner, dim, x, y, z, radius, kind, label,
                  item_uid, tags, source, confidence, created_at, last_seen_at, ttl_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["world_id"],
                    record.scope,
                    owner,
                    record.dim,
                    record.x,
                    record.y,
                    record.z,
                    record.radius,
                    record.kind,
                    record.label,
                    record.item_uid,
                    tags,
                    record.source,
                    record.confidence,
                    now,
                    now,
                    ttl_at,
                ),
            )
            self.conn.commit()
            return {"id": format_poi_id(cur.lastrowid), "merged": False}
        except sqlite3.IntegrityError:
            row = self._find_exact(ctx, record, owner)
            if row is None:
                raise
            self.conn.execute(
                """
                UPDATE poi
                SET label = ?, tags = ?, confidence = MAX(confidence, ?),
                    last_seen_at = ?, ttl_at = COALESCE(?, ttl_at)
                WHERE id = ?
                """,
                (
                    record.label,
                    dump_tags(load_tags(row["tags"]) + record.tags),
                    record.confidence,
                    now,
                    ttl_at,
                    row["id"],
                ),
            )
            self.conn.commit()
            return {"id": format_poi_id(row["id"]), "merged": True}

    def _find_exact(
        self, ctx: sqlite3.Row, record: PoiRecord, owner: str
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM poi
            WHERE world_id = ? AND dim = ? AND x = ? AND y = ? AND z = ?
              AND scope = ? AND owner = ? AND kind = ? AND item_uid = ?
            """,
            (
                ctx["world_id"],
                record.dim,
                record.x,
                record.y,
                record.z,
                record.scope,
                owner,
                record.kind,
                record.item_uid,
            ),
        ).fetchone()

    def set_preference(self, ctx_id: str, record: PreferenceRecord) -> dict:
        ctx = self.require_context(ctx_id)
        owner = self._owner_for(record.scope, ctx)
        now = now_ms()
        self.conn.execute(
            """
            INSERT INTO core(world_id, scope, owner, key, value, importance, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(world_id, scope, owner, key) DO UPDATE SET
              value = excluded.value,
              importance = excluded.importance,
              updated_at = excluded.updated_at
            """,
            (
                ctx["world_id"],
                record.scope,
                owner,
                record.key,
                record.value,
                record.importance,
                now,
            ),
        )
        self.conn.commit()
        return {"key": record.key, "scope": record.scope, "updated_at": now}

    def update_poi(self, ctx_id: str, poi_id: str, patch: PoiPatch) -> dict:
        ctx = self.require_context(ctx_id)
        poi_pk = parse_poi_id(poi_id)
        profile_owner, robot_owner = self._owners(ctx)
        row = get_visible_poi(
            self.conn,
            world_id=ctx["world_id"],
            profile_owner=profile_owner,
            robot_owner=robot_owner,
            poi_pk=poi_pk,
        )
        if row is None:
            raise ValueError(f"poi not found or not visible: {poi_id}")

        fields = patch.model_dump(exclude_unset=True)
        if "item_uid" in fields and fields["item_uid"] is None:
            fields["item_uid"] = ""
        if "label" in fields and fields["label"] is not None:
            fields["label"] = fields["label"].strip()
        if "tags" in fields:
            fields["tags"] = dump_tags(fields["tags"] or [])
        if "ttl_s" in fields:
            ttl_s = fields.pop("ttl_s")
            fields["ttl_at"] = now_ms() + ttl_s * 1000 if ttl_s is not None else None
        if not fields:
            return self._row_to_poi(row)

        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [poi_pk]
        self.conn.execute(f"UPDATE poi SET {assignments} WHERE id = ?", params)
        self.conn.commit()
        updated = self.conn.execute(
            "SELECT * FROM poi WHERE id = ?", (poi_pk,)
        ).fetchone()
        return self._row_to_poi(updated)

    def forget(
        self, ctx_id: str, poi_id: str | None = None, query: str | None = None
    ) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        if poi_id is not None:
            poi_pk = parse_poi_id(poi_id)
            row = get_visible_poi(
                self.conn,
                world_id=ctx["world_id"],
                profile_owner=profile_owner,
                robot_owner=robot_owner,
                poi_pk=poi_pk,
            )
            if row is None:
                raise ValueError(f"poi not found or not visible: {poi_id}")
            self.conn.execute("DELETE FROM poi WHERE id = ?", (poi_pk,))
            self.conn.commit()
            return {"deleted": 1}

        if query is None or not query.strip():
            raise ValueError("provide poi_id or a non-empty query")

        sql = f"""
            DELETE FROM poi
            WHERE world_id = ?
              AND label LIKE ?
              AND {visibility_clause("poi")}
        """
        cur = self.conn.execute(
            sql,
            (
                ctx["world_id"],
                f"%{query.strip()}%",
                profile_owner,
                robot_owner,
            ),
        )
        self.conn.commit()
        return {"deleted": cur.rowcount}

    # --------------------------------------------------------------------- read

    def _core_rows(self, ctx: sqlite3.Row) -> list[dict]:
        profile_owner, robot_owner = self._owners(ctx)
        sql = f"""
            SELECT key, value, scope, importance, updated_at
            FROM core
            WHERE world_id = ?
              AND {visibility_clause("core")}
            ORDER BY importance DESC, updated_at DESC
            LIMIT 50
        """
        rows = self.conn.execute(
            sql, (ctx["world_id"], profile_owner, robot_owner)
        ).fetchall()
        return [
            {
                "key": row["key"],
                "value": row["value"],
                "scope": row["scope"],
                "importance": row["importance"],
            }
            for row in rows
        ]

    @staticmethod
    def _task_tokens(task: str | None) -> list[str]:
        if not task:
            return []
        tokens = re.findall(r"[\w\-]+", task.lower(), flags=re.UNICODE)
        result = []
        for token in tokens:
            if len(token) < 3 or token in _STOPWORDS:
                continue
            result.append(token)
        return result[:6]

    def brief(
        self,
        ctx_id: str,
        *,
        task: str | None = None,
        pose: Pose | None = None,
        item_uid: str | None = None,
        kind: str | None = None,
        radius: int | None = None,
        budget_tokens: int | None = None,
    ) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        radius = self.settings.default_radius if radius is None else radius
        budget = (
            self.settings.default_budget_tokens
            if budget_tokens is None
            else budget_tokens
        )
        if item_uid is not None:
            item_uid = validate_item_uid(item_uid)

        now = now_ms()
        core = self._core_rows(ctx)

        candidates: dict[int, tuple[sqlite3.Row, float | None, bool]] = {}
        if pose is not None:
            for row, dist in nearby_rows(
                self.conn,
                world_id=ctx["world_id"],
                profile_owner=profile_owner,
                robot_owner=robot_owner,
                dim=pose.dim,
                x=pose.x,
                y=pose.y,
                z=pose.z,
                radius=radius,
                kind=kind,
                item_uid=item_uid,
            ):
                candidates[row["id"]] = (row, dist, True)

        tokens = self._task_tokens(task)
        if tokens or item_uid is not None or kind is not None:
            for row in search_rows(
                self.conn,
                world_id=ctx["world_id"],
                profile_owner=profile_owner,
                robot_owner=robot_owner,
                kind=kind,
                item_uid=item_uid,
                text_tokens=tokens,
            ):
                if row["id"] not in candidates:
                    dist = None
                    if pose is not None:
                        dist = weighted_distance(
                            pose.x, pose.y, pose.z, row["x"], row["y"], row["z"]
                        )
                    candidates[row["id"]] = (row, dist, False)

        scored = []
        for row, dist, is_nearby in candidates.values():
            scored.append(
                (
                    score_row(
                        row,
                        now_ms=now,
                        dist=dist,
                        item_exact=bool(item_uid) and row["item_uid"] == item_uid,
                    ),
                    row,
                    dist,
                    is_nearby,
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)

        items = []
        for _score, row, dist, is_nearby in scored:
            item = self._row_to_poi(row, dist)
            item["_nearby"] = is_nearby
            items.append(item)

        selected, used = apply_budget(core, items, budget)
        nearby = []
        relevant = []
        for item in selected:
            is_nearby = item.pop("_nearby", False)
            (nearby if is_nearby else relevant).append(item)
        return {
            "core": core,
            "nearby": nearby,
            "relevant": relevant,
            "stats": {
                "returned": len(selected),
                "used_tokens": used,
                "budget_tokens": budget,
            },
        }

    def map_pois(
        self,
        ctx_id: str,
        *,
        dim: int,
        center: Vec3,
        radius: int | None = None,
        fmt: str = "ascii",
    ) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        radius = self.settings.default_radius if radius is None else radius
        rows = nearby_rows(
            self.conn,
            world_id=ctx["world_id"],
            profile_owner=profile_owner,
            robot_owner=robot_owner,
            dim=dim,
            x=center.x,
            y=center.y,
            z=center.z,
            radius=radius,
            limit=self.settings.max_results,
        )
        pois = []
        for row, dist in sorted(rows, key=lambda item: item[1]):
            pois.append(self._row_to_poi(row, dist))

        result = {"format": fmt, "count": len(pois), "pois": pois}
        if fmt == "ascii":
            result["ascii"] = render_ascii(
                world_id=ctx["world_id"],
                dim=dim,
                center=(center.x, center.y, center.z),
                radius=radius,
                pois=pois,
            )
        elif fmt != "json":
            raise ValueError("format must be 'ascii' or 'json'")
        return result

    def stats(self, ctx_id: str) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        where = f"world_id = ? AND {visibility_clause('poi')}"
        params = (ctx["world_id"], profile_owner, robot_owner)
        total = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM poi WHERE {where}", params
        ).fetchone()["n"]
        by_kind = {
            row["kind"]: row["n"]
            for row in self.conn.execute(
                f"SELECT kind, COUNT(*) AS n FROM poi WHERE {where} GROUP BY kind",
                params,
            )
        }
        by_scope = {
            row["scope"]: row["n"]
            for row in self.conn.execute(
                f"SELECT scope, COUNT(*) AS n FROM poi WHERE {where} GROUP BY scope",
                params,
            )
        }
        try:
            db_bytes = self.settings.db_path.stat().st_size
        except OSError:
            db_bytes = 0
        return {
            "world_id": ctx["world_id"],
            "poi_total": total,
            "by_kind": by_kind,
            "by_scope": by_scope,
            "db_bytes": db_bytes,
        }

    def close(self) -> None:
        self.conn.close()
