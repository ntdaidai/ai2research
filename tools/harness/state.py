from __future__ import annotations

import json
import os
import sqlite3
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def config():
    with (ROOT / "harness.toml").open("rb") as f:
        return tomllib.load(f)


def transport():
    mode = os.environ.get("AI2RESEARCH_TRANSPORT", "ssh")
    if mode not in ("local", "ssh"):
        raise ValueError("AI2RESEARCH_TRANSPORT must be local or ssh")
    return mode


def review_limit():
    review = config()["review"]
    return review.get("server_max_calls", 0) if transport() == "local" else review["max_calls"]


def local_dir():
    # An absolute override lets other workspaces use this same state store.
    path = Path(os.environ.get("RESEARCH_STATE_DIR", str(ROOT / config()["project"]["state_dir"]))).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def now():
    return datetime.now(timezone.utc).isoformat()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(local_dir() / "state.sqlite3", timeout=30, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
        created_utc TEXT NOT NULL, payload TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS events_project_kind ON events(project,kind)")
    db.commit()
    return db


def record(kind, payload, project_id="paper-research"):
    event_id = uuid.uuid4().hex
    with connect() as db:
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (event_id, project_id, kind, now(), json.dumps(payload, ensure_ascii=False)))
    return {**payload, "event_id": event_id, "kind": kind}


def execution_state(db, project_id):
    row = db.execute("SELECT payload FROM events WHERE project=? AND kind='project_execution_control' ORDER BY rowid DESC LIMIT 1",
                     (project_id,)).fetchone()
    return json.loads(row[0]) if row else {"execution_state": "active"}


def require_active_project(project_id, db=None):
    if db is None:
        with connect() as connection:
            return require_active_project(project_id, connection)
    if execution_state(db, project_id)["execution_state"] == "paused":
        raise ValueError("Project is paused by user instruction; read/collect/archive remain available. Resume explicitly before new work.")


def events(project_id="paper-research", limit=50, kind=None):
    limit = max(1, min(int(limit), 500))
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE project=? AND (? IS NULL OR kind=?) ORDER BY rowid DESC LIMIT ?",
            (project_id, kind, kind, limit),
        ).fetchall()
    return [{**json.loads(r["payload"]), "event_id": r["id"], "kind": r["kind"], "created_utc": r["created_utc"]} for r in rows]


def reserve_review(project_id):
    # Budget reservation is global across projects and concurrent MCP/CLI calls.
    limit = review_limit()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT COUNT(*) FROM events WHERE kind='review_request'").fetchone()[0]
        if count >= limit:
            raise ValueError("审查调用额度已用完；需用户明确新的调用额度后更新 harness.toml。")
        request_id = uuid.uuid4().hex
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (request_id, project_id, "review_request", now(), "{}"))
    return request_id
