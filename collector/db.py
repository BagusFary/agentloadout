"""SQLite schema and helpers for the agent catalog."""
import json
import os
import sqlite3

DB_PATH = os.environ.get(
    "KATALOG_DB", os.path.join(os.path.dirname(__file__), "..", "data", "katalog.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,          -- owner/repo (lowercase)
    kind          TEXT NOT NULL,             -- mcp | skill | app
    name          TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    url           TEXT NOT NULL,
    description   TEXT,
    topics        TEXT,                      -- JSON list
    language      TEXT,
    license       TEXT,
    stars         INTEGER,
    forks         INTEGER,
    open_issues   INTEGER,
    created_at    TEXT,
    pushed_at     TEXT,
    archived      INTEGER DEFAULT 0,
    homepage      TEXT,
    category      TEXT,
    install       TEXT,                      -- JSON list of {client, label, lang, snippet}
    env_vars      TEXT,                      -- JSON list of {name, secret, description}
    risks         TEXT,                      -- JSON list of {code, label, level}
    skill_paths   TEXT,                      -- JSON list of folders that hold SKILL.md
    registry_name TEXT,
    stars_7d      INTEGER,                   -- stars gained in the last 7 days (NULL: no history yet)
    momentum      REAL,                      -- stars per day
    momentum_basis TEXT,                     -- '7d' or 'lifetime' (average since creation)
    sources       TEXT,                      -- JSON list: where we found it
    first_seen    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS star_snapshots (
    id    TEXT NOT NULL,
    day   TEXT NOT NULL,
    stars INTEGER NOT NULL,
    PRIMARY KEY (id, day)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS installs (
    id         TEXT PRIMARY KEY,             -- item id
    status     TEXT NOT NULL,                -- requested | installed | failed | removed
    detail     TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE TABLE IF NOT EXISTS translations (
    src_hash TEXT PRIMARY KEY,
    lang     TEXT NOT NULL,
    text     TEXT NOT NULL
);
"""

# additive migrations (safe to run every start)
MIGRATIONS = [
    ("installs", "ref", "TEXT"),                # e.g. mcp:rea  |  skill:local-skill-...  |  app:<pkg>
    ("installs", "package", "TEXT"),            # npm/pypi package or repo for version checks
    ("installs", "installed_version", "TEXT"),  # version or commit sha
    ("installs", "latest_version", "TEXT"),
    ("installs", "enabled", "INTEGER"),         # 1 on, 0 off, NULL unknown
    ("installs", "skill_path", "TEXT"),         # folder inside the repo for skills
    ("installs", "synced_at", "TEXT"),
    ("items", "skills", "TEXT"),                # JSON list of {path, name, description}
    ("items", "description_id", "TEXT"),        # Indonesian translation
    ("items", "default_branch", "TEXT"),
]


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL: readers never block the writer, and a crash mid-write leaves the last committed state intact
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.commit()
    return con


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
