PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE world (
  world_id     TEXT PRIMARY KEY,
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL
);

CREATE TABLE context (
  ctx_id       TEXT PRIMARY KEY,
  world_id     TEXT NOT NULL REFERENCES world(world_id),
  profile_id   TEXT NOT NULL,
  robot_id     TEXT NOT NULL,
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL,
  revoked      INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);

CREATE INDEX idx_context_lookup ON context(world_id, profile_id, robot_id);

CREATE TABLE poi (
  id           INTEGER PRIMARY KEY,
  world_id     TEXT NOT NULL REFERENCES world(world_id),
  scope        TEXT NOT NULL CHECK (scope IN ('world', 'profile', 'robot')),
  owner        TEXT NOT NULL DEFAULT '',
  dim          INTEGER NOT NULL,
  x            INTEGER NOT NULL,
  y            INTEGER NOT NULL CHECK (y BETWEEN 0 AND 255),
  z            INTEGER NOT NULL,
  radius       INTEGER NOT NULL DEFAULT 0 CHECK (radius >= 0),
  kind         TEXT NOT NULL,
  label        TEXT NOT NULL,
  item_uid     TEXT NOT NULL DEFAULT '',
  tags         TEXT NOT NULL DEFAULT '',
  source       TEXT NOT NULL CHECK (source IN ('observed', 'told', 'inferred')),
  confidence   REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
  created_at   INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  ttl_at       INTEGER,
  CHECK (item_uid = '' OR (instr(item_uid, ':') > 0 AND item_uid NOT GLOB '[0-9]*')),
  UNIQUE (world_id, dim, x, y, z, scope, owner, kind, item_uid)
);

CREATE INDEX idx_poi_scope ON poi(world_id, scope, owner);
CREATE INDEX idx_poi_item  ON poi(world_id, item_uid) WHERE item_uid <> '';
CREATE INDEX idx_poi_kind  ON poi(world_id, kind);

CREATE VIRTUAL TABLE poi_rtree USING rtree(
  id,
  dim_min, dim_max,
  x_min, x_max,
  y_min, y_max,
  z_min, z_max
);

CREATE TRIGGER poi_ai AFTER INSERT ON poi BEGIN
  INSERT INTO poi_rtree(id, dim_min, dim_max, x_min, x_max, y_min, y_max, z_min, z_max)
  VALUES (new.id, new.dim, new.dim,
          new.x - new.radius, new.x + new.radius,
          new.y - new.radius, new.y + new.radius,
          new.z - new.radius, new.z + new.radius);
END;

CREATE TRIGGER poi_au AFTER UPDATE OF dim, x, y, z, radius ON poi BEGIN
  DELETE FROM poi_rtree WHERE id = old.id;
  INSERT INTO poi_rtree(id, dim_min, dim_max, x_min, x_max, y_min, y_max, z_min, z_max)
  VALUES (new.id, new.dim, new.dim,
          new.x - new.radius, new.x + new.radius,
          new.y - new.radius, new.y + new.radius,
          new.z - new.radius, new.z + new.radius);
END;

CREATE TRIGGER poi_ad AFTER DELETE ON poi BEGIN
  DELETE FROM poi_rtree WHERE id = old.id;
END;

CREATE TABLE core (
  world_id   TEXT NOT NULL REFERENCES world(world_id),
  scope      TEXT NOT NULL CHECK (scope IN ('world', 'profile', 'robot')),
  owner      TEXT NOT NULL DEFAULT '',
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 1.0 CHECK (importance BETWEEN 0 AND 1),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (world_id, scope, owner, key)
);

CREATE INDEX idx_core_lookup ON core(world_id, scope, owner);
