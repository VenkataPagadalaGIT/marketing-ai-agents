"""Steps 2 and 6: dedupe and remember. SQLite locally; in production move these four tables
to Postgres (that needs a driver such as psycopg, not only a new path)."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .schemas import DigestEntry, Item, RunStats

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  content_hash TEXT PRIMARY KEY, company TEXT, source TEXT, url TEXT, title TEXT,
  first_seen TEXT, relevance INTEGER, category TEXT, reason TEXT, sent INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, stats_json TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
  url TEXT PRIMARY KEY, text TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS digests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, sent_at TEXT, body_json TEXT
);
"""


@contextmanager
def db(path: str | None = None):
    path = path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def filter_new(items: list[Item], path: str | None = None) -> list[Item]:
    with db(path) as conn:
        seen = {r[0] for r in conn.execute("SELECT content_hash FROM items")}
    return [i for i in items if i.content_hash not in seen]


def remember(items: list[Item], judgements: dict[str, dict], path: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db(path) as conn:
        for i in items:
            j = judgements.get(i.content_hash, {})
            conn.execute(
                "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,0)",
                (i.content_hash, i.company, i.source, i.url, i.title, now,
                 j.get("relevance"), j.get("category"), j.get("reason")),
            )


def mark_sent(hashes: list[str], path: str | None = None) -> None:
    with db(path) as conn:
        conn.executemany("UPDATE items SET sent=1 WHERE content_hash=?", [(h,) for h in hashes])


def log_run(stats: RunStats, entries: list[DigestEntry], path: str | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db(path) as conn:
        cur = conn.execute("INSERT INTO runs (started_at, stats_json) VALUES (?,?)",
                           (now, stats.model_dump_json()))
        if entries:
            conn.execute("INSERT INTO digests (run_id, sent_at, body_json) VALUES (?,?,?)",
                         (cur.lastrowid, now, json.dumps([e.model_dump() for e in entries])))
        return cur.lastrowid


def get_snapshot(url: str, path: str | None = None) -> str | None:
    with db(path) as conn:
        row = conn.execute("SELECT text FROM snapshots WHERE url=?", (url,)).fetchone()
    return row[0] if row else None


def save_snapshot(url: str, text: str, path: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db(path) as conn:
        conn.execute("INSERT INTO snapshots (url, text, updated_at) VALUES (?,?,?) "
                     "ON CONFLICT(url) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
                     (url, text, now))
