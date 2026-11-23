"""A small local presentation view. Reads recorded state; never dispatches work."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
from urllib.parse import parse_qs, quote, urlsplit

from .state import ROOT, config

WEB = ROOT / "web" / "showcase"
OUTPUT_KINDS = {"manuscript_pdf", "figure_png", "figure_pdf", "diagram_pdf"}
OUTPUT_SUFFIXES = {".pdf", ".png"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
ACTIVE = {"queued", "starting", "waiting_resources", "running", "evaluating"}


def state_directory():
    return Path(os.environ.get("RESEARCH_STATE_DIR", ROOT / config()["project"]["state_dir"])).resolve()


@contextmanager
def read_database(directory):
    """Do not create/migrate the database or trigger normal service maintenance."""
    db = sqlite3.connect((directory / "state.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or value in (".", ".."):
        raise ValueError("Invalid identifier")
    return value


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _artifacts(db, project_id):
    if "workflow_artifacts" not in _tables(db):
        return []
    # One current registration per path, not the count of historical revisions.
    rows = db.execute("""SELECT artifact_id, path, kind, sha256, bytes, created_utc FROM (
        SELECT artifact_id, json_extract(payload,'$.path') AS path,
            json_extract(payload,'$.kind') AS kind, json_extract(payload,'$.sha256') AS sha256,
            json_extract(payload,'$.bytes') AS bytes, json_extract(payload,'$.created_utc') AS created_utc,
            row_number() OVER (PARTITION BY json_extract(payload,'$.path') ORDER BY rowid DESC) AS revision,
            rowid AS position FROM workflow_artifacts WHERE project_id=?)
        WHERE revision=1 ORDER BY position DESC""", (project_id,)).fetchall()
    return [dict(row) for row in rows]


def _reviews(db, project_id):
    if "packet_reviews" not in _tables(db):
        return []
    packets = db.execute("""SELECT json_extract(payload,'$.packet_id') AS packet_id,
        json_extract(payload,'$.stage') AS stage, created_utc FROM events
        WHERE project=? AND kind='review_packet' ORDER BY rowid DESC""", (project_id,)).fetchall()
    latest = {}
    for packet in packets:
        if packet["stage"] in latest:
            continue
        reviews = []
        for row in db.execute("""SELECT reviewer, json_extract(payload,'$.review.score') AS score,
            json_extract(payload,'$.review.blockers') AS blockers,
            json_extract(payload,'$.review.issues') AS issues FROM packet_reviews
            WHERE packet=? AND project=? ORDER BY reviewer""", (packet["packet_id"], project_id)):
            issues = json.loads(row["issues"] or "[]")
            reviews.append({"reviewer": row["reviewer"], "score": row["score"],
                            "major_issues": sum(i.get("severity") in ("major", "critical") for i in issues),
                            "blockers": len(json.loads(row["blockers"] or "[]"))})
        problems = any(r["major_issues"] or r["blockers"] or (r["score"] is not None and r["score"] < 7)
                       for r in reviews)
        roles = {r["reviewer"] for r in reviews}
        latest[packet["stage"]] = {**dict(packet), "reviews": reviews,
            "recorded_status": "revision_requested" if problems else
                               "awaiting_reviews" if not {"agent", "deepseek"}.issubset(roles) else "reviews_recorded"}
    return list(latest.values())


def _activity(db, project_id):
    from .monitoring import _ACTIONS
    kinds = tuple(_ACTIONS)
    rows = db.execute(f"""SELECT id, kind, created_utc,
        substr(COALESCE(NULLIF(json_extract(payload,'$.summary'),''),
            json_extract(payload,'$.text'), json_extract(payload,'$.reason'),
            json_extract(payload,'$.title'), ''),1,160) AS message
        FROM events WHERE project=? AND kind IN ({','.join('?' for _ in kinds)})
        ORDER BY rowid DESC LIMIT 4""", (project_id, *kinds)).fetchall()
    return [{"id": r["id"], "time": r["created_utc"], "category": _ACTIONS[r["kind"]][0],
             "action": _ACTIONS[r["kind"]][1], "message": " ".join(r["message"].split())} for r in rows]


def snapshot(project_id=None, *, directory=None):
    from .showcase_catalog import research_references, tool_inventory
    directory = Path(directory).resolve() if directory is not None else state_directory()
    result = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "mode": "recorded_state", "projects": [], "project": None, "tools": tool_inventory()}
    if not (directory / "state.sqlite3").is_file():
        return result
    with read_database(directory) as db:
        if not {"events", "workflow_projects"}.issubset(_tables(db)):
            return result
        manifests = db.execute("SELECT project_id, manifest FROM workflow_projects ORDER BY rowid DESC").fetchall()
        controls = {}
        for row in db.execute("""SELECT project, json_extract(payload,'$.execution_state') AS state
            FROM events WHERE kind='project_execution_control' ORDER BY rowid"""):
            controls[row["project"]] = row["state"]
        for row in manifests:
            manifest = json.loads(row["manifest"])
            result["projects"].append({"id": row["project_id"], "title": manifest.get("title") or row["project_id"],
                "execution_state": controls.get(row["project_id"], "active"),
                "evidence_kind": manifest.get("evidence_kind", "unknown")})
        result["projects"].sort(key=lambda p: (p["evidence_kind"] != "research", p["execution_state"] == "paused"))
        if not result["projects"]:
            return result
        selected = project_id or result["projects"][0]["id"]
        _identifier(selected)
        project = next((p.copy() for p in result["projects"] if p["id"] == selected), None)
        if project is None:
            raise KeyError("Project not found")
        from .recovery import _job_inventory
        jobs = _job_inventory(selected, db)
        counts = dict(Counter(j["state"] for j in jobs))
        project.update(job_counts=counts, jobs_total=len(jobs),
                       running_jobs=sum(v for k, v in counts.items() if k in ACTIVE),
                       jobs=[{"id": j["job_id"], "state": j["state"], "collected": j["collected"]}
                             for j in reversed(jobs)])
        artifacts = _artifacts(db, selected)
        kinds = Counter(a["kind"] for a in artifacts)
        project["artifact_count"] = len(artifacts)
        project["artifact_kinds"] = dict(kinds)
        outputs, categories = [], set()
        for artifact in artifacts:
            kind = artifact["kind"]
            category = "paper" if kind == "manuscript_pdf" else "diagram" if kind == "diagram_pdf" else "figure"
            if kind not in OUTPUT_KINDS or category in categories or PurePosixPath(artifact["path"]).suffix not in OUTPUT_SUFFIXES:
                continue
            categories.add(category)
            outputs.append({**artifact, "category": category,
                "url": "/api/artifact?project=" + quote(selected) + "&id=" + quote(artifact["artifact_id"])})
        project["outputs"] = sorted(outputs, key=lambda a: {"paper": 0, "diagram": 1, "figure": 2}[a["category"]])
        project["reviews"] = _reviews(db, selected)
        project["activity"] = _activity(db, selected)
        project["task_counts"] = {}
        if "workflow_tasks" in _tables(db):
            project["task_counts"] = {r[0]: r[1] for r in db.execute("""SELECT json_extract(payload,'$.state'), COUNT(*)
                FROM workflow_tasks WHERE project_id=? GROUP BY json_extract(payload,'$.state')""", (selected,))}
        row = db.execute("SELECT MAX(created_utc) FROM events WHERE project=?", (selected,)).fetchone()
        project["last_event_at"] = row[0]
        from .progress import project_progress
        manifest = json.loads(next(r["manifest"] for r in manifests if r["project_id"] == selected))
        project["progress"] = project_progress(db, directory, manifest, project, artifacts, jobs, project["reviews"])
        project["references"] = research_references(directory, selected, manifest, artifacts)
        result["project"] = project
    return result


def artifact_bytes(directory, project_id, artifact_id):
    """Only registered current presentation outputs, with matching bytes, are served."""
    _identifier(project_id)
    _identifier(artifact_id)
    directory = Path(directory).resolve()
    with read_database(directory) as db:
        artifact = next((a for a in _artifacts(db, project_id) if a["artifact_id"] == artifact_id), None)
    if artifact is None or artifact["kind"] not in OUTPUT_KINDS:
        raise KeyError("Output not found")
    relative = PurePosixPath(artifact["path"])
    if relative.is_absolute() or ".." in relative.parts or "\\" in artifact["path"] or relative.suffix not in OUTPUT_SUFFIXES:
        raise ValueError("Invalid output path")
    base = directory / "projects"
    target = base / project_id / Path(*relative.parts)
    cursor = base
    for part in (project_id, *relative.parts):
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not presentation outputs")
        cursor = cursor / part
    if target.is_symlink() or not target.resolve().is_relative_to((base / project_id).resolve()):
        raise ValueError("Invalid output path")
    if not target.is_file():
        raise FileNotFoundError("Output is no longer available")
    if target.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Output exceeds the 32 MiB presentation limit")
    content = target.read_bytes()
    if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
        raise ValueError("Output changed after registration; refresh its project registration")
    return content, target.name, mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def make_server(host="127.0.0.1", port=8765, *, directory=None, project_id=None):
    directory = Path(directory).resolve() if directory is not None else state_directory()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _respond(self, status, body, content_type, *, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            self._respond(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            if host in {"127.0.0.1", "localhost"} and urlsplit("http://" + self.headers.get("Host", "")).hostname not in {"127.0.0.1", "localhost"}:
                return self._json(403, {"error": "Local requests only"})
            route = urlsplit(self.path)
            query = parse_qs(route.query)
            try:
                if route.path == "/api/snapshot":
                    return self._json(200, snapshot(query.get("project", [project_id])[0], directory=directory))
                if route.path == "/api/artifact":
                    content, filename, content_type = artifact_bytes(directory, query.get("project", [""])[0],
                                                                     query.get("id", [""])[0])
                    return self._respond(200, content, content_type, filename=filename)
                assets = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css",
                          "/app.js": "app.js", "/catalog.js": "catalog.js", "/favicon.svg": "favicon.svg"}
                if route.path not in assets:
                    return self._json(404, {"error": "Not found"})
                asset = WEB / assets[route.path]
                return self._respond(200, asset.read_bytes(), (mimetypes.guess_type(asset.name)[0] or "text/plain") + "; charset=utf-8")
            except (KeyError, FileNotFoundError):
                self._json(404, {"error": "未找到项目或成果文件，请刷新后重试。"})
            except ValueError:
                self._json(409, {"error": "成果登记与当前文件不一致，请更新登记后重试。"})
            except (sqlite3.Error, OSError):
                self._json(503, {"error": "暂时无法读取项目记录，请稍后刷新。"})

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open the lightweight, read-only ai2research showcase")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: local machine only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project", help="Initially selected project ID")
    args = parser.parse_args(argv)
    if args.project:
        _identifier(args.project)
    with make_server(args.host, args.port, project_id=args.project) as server:
        host, port = server.server_address[:2]
        print(f"ai2research 展示台：http://{host}:{port}", flush=True)
        print("只读项目记录 · Ctrl+C 退出", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
