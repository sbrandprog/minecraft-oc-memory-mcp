from __future__ import annotations

import re
import secrets
import sqlite3

from .config import Settings
from .db import connect, migrate
from .rank import (
    EPISODE_MIN_PREFIX,
    apply_budget,
    apply_episode_budget,
    episode_text_score,
    score_episode,
    score_row,
)
from .render import render_ascii
from .schemas import (
    EpisodeRecord,
    PoiPatch,
    PoiRecord,
    Pose,
    PreferenceRecord,
    Vec3,
)
from .spatial import (
    get_visible_poi,
    nearby_rows,
    search_rows,
    visibility_clause,
    weighted_distance,
)
from .util import (
    dump_list,
    dump_tags,
    escape_like,
    format_episode_id,
    format_poi_id,
    load_list,
    load_tags,
    normalize_text,
    now_ms,
    parse_episode_id,
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

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> dict:
        return {
            "id": format_episode_id(row["id"]),
            "goal": row["goal"],
            "outcome": row["outcome"],
            "obstacles": load_list(row["obstacles"]),
            "lesson": row["lesson"],
            "tags": load_list(row["tags"]),
            "scope": row["scope"],
            "importance": row["importance"],
            "occurrences": row["occurrences"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
        }

    @staticmethod
    def _episode_search_text(
        goal_norm: str,
        obstacles: list[str],
        lesson_norm: str,
        tags_norm: list[str],
    ) -> str:
        parts = [goal_norm, lesson_norm]
        parts.extend(normalize_text(item) for item in obstacles)
        parts.extend(tags_norm)
        return " ".join(part for part in parts if part)

    @staticmethod
    def _doc_tokens(value: str) -> set[str]:
        return set(re.findall(r"[\w\-]+", normalize_text(value), flags=re.UNICODE))

    def _episode_field_tokens(self, row: sqlite3.Row) -> dict[str, set[str]]:
        return {
            "goal": self._doc_tokens(row["goal"]),
            "lesson": self._doc_tokens(row["lesson"]),
            "obstacles": self._doc_tokens(" ".join(load_list(row["obstacles"]))),
            "tags": self._doc_tokens(" ".join(load_list(row["tags"]))),
        }

    @staticmethod
    def _merge_unique(first: list[str], second: list[str]) -> list[str]:
        result = list(first)
        for item in second:
            if item not in result:
                result.append(item)
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

    def _find_episode(
        self,
        *,
        world_id: str,
        scope: str,
        owner: str,
        goal_norm: str,
        lesson_norm: str,
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM episode
            WHERE world_id = ? AND scope = ? AND owner = ?
              AND goal_norm = ? AND lesson_norm = ?
            ORDER BY id
            LIMIT 1
            """,
            (world_id, scope, owner, goal_norm, lesson_norm),
        ).fetchone()

    def _merge_episode(
        self, existing: sqlite3.Row, record: EpisodeRecord, now: int
    ) -> None:
        goal_norm = normalize_text(record.goal)
        lesson_norm = normalize_text(record.lesson)
        obstacles = self._merge_unique(
            load_list(existing["obstacles"]), record.obstacles
        )[:10]
        tags = self._merge_unique(load_list(existing["tags"]), record.tags)[:32]
        merged_tags_norm = sorted({normalize_text(tag) for tag in tags if tag})
        search_text = self._episode_search_text(
            goal_norm, obstacles, lesson_norm, merged_tags_norm
        )
        self.conn.execute(
            """
            UPDATE episode
            SET goal = ?,
                obstacles = ?,
                tags = ?,
                tags_norm = ?,
                search_text = ?,
                outcome = ?,
                importance = MAX(importance, ?),
                last_seen_at = ?,
                occurrences = occurrences + 1
            WHERE id = ?
            """,
            (
                record.goal,
                dump_list(obstacles),
                dump_tags(tags),
                dump_tags(merged_tags_norm),
                search_text,
                record.outcome,
                record.importance,
                now,
                existing["id"],
            ),
        )

    def remember_episode(self, ctx_id: str, record: EpisodeRecord) -> dict:
        ctx = self.require_context(ctx_id)
        owner = self._owner_for(record.scope, ctx)
        now = now_ms()
        goal_norm = normalize_text(record.goal)
        lesson_norm = normalize_text(record.lesson)
        tags_norm = sorted({normalize_text(tag) for tag in record.tags if tag})
        search_text = self._episode_search_text(
            goal_norm, record.obstacles, lesson_norm, tags_norm
        )

        existing = self._find_episode(
            world_id=ctx["world_id"],
            scope=record.scope,
            owner=owner,
            goal_norm=goal_norm,
            lesson_norm=lesson_norm,
        )
        if existing is not None:
            self._merge_episode(existing, record, now)
            self.conn.commit()
            return {"id": format_episode_id(existing["id"]), "merged": True}

        cur = self.conn.execute(
            """
            INSERT INTO episode(
              world_id, scope, owner, goal, outcome, obstacles, lesson, tags,
              importance, occurrences, created_at, last_seen_at,
              goal_norm, lesson_norm, tags_norm, search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(world_id, scope, owner, goal_norm, lesson_norm)
            DO NOTHING
            """,
            (
                ctx["world_id"],
                record.scope,
                owner,
                record.goal,
                record.outcome,
                dump_list(record.obstacles),
                record.lesson,
                dump_tags(record.tags),
                record.importance,
                now,
                now,
                goal_norm,
                lesson_norm,
                dump_tags(tags_norm),
                search_text,
            ),
        )
        if cur.rowcount == 0:
            # A concurrent writer inserted the same merge key between our
            # SELECT and INSERT; merge into that row instead.
            existing = self._find_episode(
                world_id=ctx["world_id"],
                scope=record.scope,
                owner=owner,
                goal_norm=goal_norm,
                lesson_norm=lesson_norm,
            )
            if existing is None:
                raise ValueError("episode merge conflict could not be resolved")
            self._merge_episode(existing, record, now)
            self.conn.commit()
            return {"id": format_episode_id(existing["id"]), "merged": True}

        self.conn.commit()
        return {"id": format_episode_id(cur.lastrowid), "merged": False}

    @staticmethod
    def _tag_score(filters: list[str], doc_tags: list[str]) -> float:
        if not filters:
            return 0.0
        doc = set(doc_tags)
        return sum(1 for tag in filters if tag in doc) / len(filters)

    def _rank_episodes(
        self,
        ctx: sqlite3.Row,
        *,
        query: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
        threshold: float = 0.20,
    ) -> list[tuple[float, sqlite3.Row, list[str]]]:
        profile_owner, robot_owner = self._owners(ctx)
        query_norm = normalize_text(query or "")
        if query is not None and query.strip() and not query_norm:
            return []
        tokens = self._task_tokens(query_norm, limit=8) if query_norm else []
        tag_filters = sorted(
            {normalize_text(tag) for tag in (tags or []) if tag.strip()}
        )
        if query_norm and not tokens and not tag_filters:
            return []

        sql = f"""
            SELECT e.*
            FROM episode e
            WHERE e.world_id = ?
              AND {visibility_clause("e")}
        """
        params: list[object] = [
            ctx["world_id"],
            profile_owner,
            robot_owner,
        ]
        if tokens:
            prefixes = self._search_prefixes(tokens)
            sql += " AND (" + " OR ".join(
                "e.search_text LIKE ?" for _ in prefixes
            ) + ")"
            params.extend(f"%{prefix}%" for prefix in prefixes)
        if tag_filters:
            sql += " AND " + " AND ".join(
                "e.tags_norm LIKE ?" for _ in tag_filters
            )
            params.extend(f'%"{tag}"%' for tag in tag_filters)
        sql += " ORDER BY e.last_seen_at DESC LIMIT ?"
        params.append(max(300, limit * 20))

        rows = self.conn.execute(sql, params).fetchall()
        now = now_ms()
        scored: list[tuple[float, sqlite3.Row, list[str]]] = []
        for row in rows:
            if tokens:
                text_score, matched = episode_text_score(
                    tokens,
                    self._episode_field_tokens(row),
                    full_query=query_norm,
                    search_text=row["search_text"],
                )
                if text_score < threshold:
                    continue
                tag_score = self._tag_score(
                    tag_filters, load_list(row["tags_norm"])
                )
            else:
                text_score, matched = 0.0, []
                tag_score = self._tag_score(
                    tag_filters, load_list(row["tags_norm"])
                )
            score = score_episode(
                row,
                now_ms=now,
                text_score=text_score,
                tag_score=tag_score,
                query_used=bool(tokens),
            )
            scored.append((score, row, matched))

        scored.sort(
            key=lambda item: (
                item[0],
                item[1]["last_seen_at"],
                item[1]["id"],
            ),
            reverse=True,
        )
        return scored[:limit]

    def recall_episodes(
        self,
        ctx_id: str,
        *,
        query: str | None = None,
        tags: list[str] | None = None,
        limit: int = 5,
    ) -> dict:
        ctx = self.require_context(ctx_id)
        limit = max(1, min(limit, 50))
        ranked = self._rank_episodes(
            ctx, query=query, tags=tags, limit=limit, threshold=0.20
        )
        episodes = []
        for score, row, matched in ranked:
            item = self._row_to_episode(row)
            item["score"] = round(score, 3)
            item["matched"] = matched
            episodes.append(item)
        return {"episodes": episodes, "count": len(episodes)}

    def forget_episode(
        self,
        ctx_id: str,
        episode_id: str | None = None,
        query: str | None = None,
    ) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        if episode_id is not None:
            episode_pk = parse_episode_id(episode_id)
            row = self.conn.execute(
                f"""
                SELECT * FROM episode
                WHERE id = ? AND world_id = ?
                  AND {visibility_clause("episode")}
                """,
                (
                    episode_pk,
                    ctx["world_id"],
                    profile_owner,
                    robot_owner,
                ),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"episode not found or not visible: {episode_id}"
                )
            self.conn.execute("DELETE FROM episode WHERE id = ?", (episode_pk,))
            self.conn.commit()
            return {"deleted": 1}

        if query is None or not query.strip():
            raise ValueError("provide episode_id or a non-empty query")

        query_norm = normalize_text(query)
        if not query_norm:
            raise ValueError("query must contain searchable text")

        cur = self.conn.execute(
            f"""
            DELETE FROM episode
            WHERE world_id = ?
              AND search_text LIKE ? ESCAPE '\\'
              AND {visibility_clause("episode")}
            """,
            (
                ctx["world_id"],
                f"%{escape_like(query_norm)}%",
                profile_owner,
                robot_owner,
            ),
        )
        self.conn.commit()
        return {"deleted": cur.rowcount}

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
    def _task_tokens(task: str | None, *, limit: int = 6) -> list[str]:
        if not task:
            return []
        tokens = re.findall(
            r"[\w\-]+", normalize_text(task), flags=re.UNICODE
        )
        result = []
        for token in tokens:
            if len(token) < 3 or token in _STOPWORDS:
                continue
            result.append(token)
        return result[:limit]

    @staticmethod
    def _search_prefixes(tokens: list[str]) -> list[str]:
        # episode_text_score matches tokens sharing at least
        # EPISODE_MIN_PREFIX leading characters, so this prefix is a sound
        # (and cheap) SQL prefilter. Anything it lets through is verified by
        # the scorer afterwards.
        return sorted({token[:EPISODE_MIN_PREFIX] for token in tokens})

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
        include_lessons: bool = True,
        lesson_budget_tokens: int | None = None,
    ) -> dict:
        ctx = self.require_context(ctx_id)
        profile_owner, robot_owner = self._owners(ctx)
        radius = self.settings.default_radius if radius is None else radius
        budget = (
            self.settings.default_budget_tokens
            if budget_tokens is None
            else budget_tokens
        )
        lesson_budget = (
            self.settings.lesson_budget_tokens
            if lesson_budget_tokens is None
            else max(0, lesson_budget_tokens)
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

        lessons: list[dict] = []
        lesson_used = 0
        if (
            include_lessons
            and tokens
            and lesson_budget > 0
            and self.settings.max_lessons > 0
        ):
            ranked = self._rank_episodes(
                ctx,
                query=task,
                limit=self.settings.max_lessons,
                threshold=0.25,
            )
            compact = []
            for _score, row, _matched in ranked:
                item = self._row_to_episode(row)
                compact.append(
                    {
                        "id": item["id"],
                        "goal": item["goal"],
                        "outcome": item["outcome"],
                        "obstacles": item["obstacles"],
                        "lesson": item["lesson"],
                        "tags": item["tags"],
                        "occurrences": item["occurrences"],
                    }
                )
            lessons, lesson_used = apply_episode_budget(
                compact, lesson_budget
            )

        return {
            "core": core,
            "nearby": nearby,
            "relevant": relevant,
            "lessons": lessons,
            "stats": {
                "returned": len(selected),
                "used_tokens": used,
                "budget_tokens": budget,
                "lessons_returned": len(lessons),
                "lesson_tokens": lesson_used,
                "lesson_budget_tokens": lesson_budget,
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
        episode_where = (
            f"world_id = ? AND {visibility_clause('episode')}"
        )
        episode_total = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM episode WHERE {episode_where}", params
        ).fetchone()["n"]
        episodes_by_outcome = {
            row["outcome"]: row["n"]
            for row in self.conn.execute(
                f"""
                SELECT outcome, COUNT(*) AS n
                FROM episode
                WHERE {episode_where}
                GROUP BY outcome
                """,
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
            "episode_total": episode_total,
            "episodes_by_outcome": episodes_by_outcome,
            "db_bytes": db_bytes,
        }

    def close(self) -> None:
        self.conn.close()
