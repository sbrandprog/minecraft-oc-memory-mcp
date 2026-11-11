CREATE TABLE episode (
  id           INTEGER PRIMARY KEY,
  world_id     TEXT NOT NULL REFERENCES world(world_id),
  scope        TEXT NOT NULL CHECK (scope IN ('world', 'profile', 'robot')),
  owner        TEXT NOT NULL DEFAULT '',
  goal         TEXT NOT NULL,
  outcome      TEXT NOT NULL DEFAULT 'partial'
               CHECK (outcome IN ('success', 'partial', 'failed')),
  obstacles    TEXT NOT NULL DEFAULT '[]',
  lesson       TEXT NOT NULL,
  tags         TEXT NOT NULL DEFAULT '[]',
  importance   REAL NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
  occurrences  INTEGER NOT NULL DEFAULT 1 CHECK (occurrences >= 1),
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  goal_norm    TEXT NOT NULL,
  lesson_norm  TEXT NOT NULL,
  tags_norm    TEXT NOT NULL DEFAULT '[]',
  search_text  TEXT NOT NULL
);

CREATE INDEX idx_episode_scope  ON episode(world_id, scope, owner);
CREATE INDEX idx_episode_recent ON episode(world_id, last_seen_at DESC);
CREATE UNIQUE INDEX idx_episode_merge
  ON episode(world_id, scope, owner, goal_norm, lesson_norm);
