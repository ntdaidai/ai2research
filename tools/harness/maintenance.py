"""Automatic local housekeeping at tool boundaries, without model calls or remote I/O."""
from contextvars import ContextVar
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

from .state import connect, local_dir, now

_DEPTH = ContextVar("harness_tool_depth", default=0)
_CRITICAL = {"start_project", "start_from_request", "record_decision", "update_task", "repair_changed_task_evidence", "set_project_execution_state",
             "collect_job_results", "attach_job_evidence", "resume_project", "record_review", "run_external_review",
             "assemble_manuscript", "export_delivery", "research_workflow", "submit_experiment", "claim_task"}


def _diagnose(message):
    try:
        print(message, file=sys.stderr)
    except Exception:
        # Diagnostics must never replace the tool's result or original error.
        pass


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS project_maintenance (
        project TEXT PRIMARY KEY, watermark INTEGER NOT NULL DEFAULT 0,
        updated_unix REAL NOT NULL DEFAULT 0, owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
        checkpoint_path TEXT, error_type TEXT)""")


def _folder(project_id):
    base = local_dir() / "maintenance"
    folder = base / hashlib.sha256(project_id.encode()).hexdigest()[:24]
    if base.is_symlink() or folder.is_symlink():
        raise ValueError("Maintenance directories must not be symbolic links")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".maintenance-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _snapshot(project_id):
    from .monitoring import project_overview, process_log
    with connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_projects'").fetchone()
        registered = exists and db.execute("SELECT 1 FROM workflow_projects WHERE project_id=?", (project_id,)).fetchone()
        decision = db.execute("""SELECT substr(json_extract(payload, '$.text'),1,1200) FROM events
            WHERE project=? AND kind='decision' ORDER BY rowid DESC LIMIT 1""", (project_id,)).fetchone()
        expired = []
        if registered:
            expired = [row[0] for row in db.execute("""SELECT task_id FROM workflow_tasks
                WHERE project_id=? AND json_extract(payload, '$.state')='running'
                AND json_extract(payload, '$.lease_expires_unix')<=? LIMIT 20""", (project_id, time.time()))]
    overview = project_overview(project_id) if registered else {"project_id": project_id, "title": project_id}
    log = process_log(project_id, 50)
    issues = []
    if expired:
        issues.append({"code": "expired_task_leases", "task_ids": expired,
                       "next_action": "Inspect saved work before reclaiming the existing tasks"})
    if overview.get("jobs_total", 0) and any(task["state"] == "pending" and task["role"] in
            ("method_designer", "implementation", "evaluation") for task in overview.get("open_tasks", [])):
        issues.append({"code": "board_evidence_gap",
                       "next_action": "Register actual outputs and reconcile pending handoffs before duplicating work"})
    return {"overview": overview, "latest_decision": decision[0] if decision else "",
            "issues": issues, "log_cursor": log["next_event_id"], "log_lines": log["lines"]}


def maintain_project(project_id, operation="", error_type="", force=False):
    """Checkpoint after changed tool activity; throttle nonterminal bursts across processes."""
    owner = uuid.uuid4().hex
    claimed = False
    try:
        with connect() as db:
            _schema(db)
            db.execute("BEGIN IMMEDIATE")
            watermark = db.execute("SELECT COALESCE(MAX(rowid),0) FROM events WHERE project=?", (project_id,)).fetchone()[0]
            row = db.execute("SELECT * FROM project_maintenance WHERE project=?", (project_id,)).fetchone()
            current = time.time()
            if row and row["lease_until"] > current:
                return {"state": "retry_wait" if row["error_type"] else "busy"}
            if row and not row["error_type"] and not error_type:
                if row["watermark"] == watermark and current - row["updated_unix"] < 30:
                    return {"state": "unchanged"}
                if not force and current - row["updated_unix"] < 30:
                    return {"state": "throttled"}
            db.execute("""INSERT INTO project_maintenance(project,owner,lease_until) VALUES (?,?,?)
                ON CONFLICT(project) DO UPDATE SET owner=excluded.owner,lease_until=excluded.lease_until""",
                (project_id, owner, current + 120))
        claimed = True
        folder = _folder(project_id)
        snapshot = _snapshot(project_id)
        from .collection import cleanup_abandoned_collections
        cleanup = cleanup_abandoned_collections()
        generation = uuid.uuid4().hex
        checkpoint = {"project_id": project_id, "generation": generation, "updated_utc": now(),
                      "event_watermark": watermark, "operation": operation, "operation_error_type": error_type,
                      **snapshot, "cleanup": cleanup,
                      "meaning": "Recorded handoff; scientific evidence and completion are checked by workflow/review gates"}
        overview = snapshot["overview"]
        handoff = [f"# {overview.get('title') or project_id}", f"generation: {generation}",
                   f"状态：{overview.get('execution_state', 'recorded')}",
                   f"最新决定：{snapshot['latest_decision'] or '尚未记录'}",
                   "任务计数：" + json.dumps(overview.get("task_counts", {}), ensure_ascii=False),
                   "待恢复作业：" + str(overview.get("pending_jobs_total", 0))]
        handoff += [f"待核对：{issue['code']}；{issue['next_action']}" for issue in snapshot["issues"]]
        handoff += [f"任务：{task['task_id']}；{task['role']}；{task.get('prompt_path', '')}"
                    for task in overview.get("open_tasks", [])]
        handoff += ["详细产物与证据由原项目目录和状态库保存。"]
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner FROM project_maintenance WHERE project=?", (project_id,)).fetchone()
            if row[0] != owner:
                return {"state": "superseded"}
            # The JSON checkpoint is the commit marker for the derived human views.
            _atomic_write(folder / "handoff.md", ("\n\n".join(handoff) + "\n").encode())
            _atomic_write(folder / "progress.log", (f"# generation: {generation}\n" + "\n".join(snapshot["log_lines"]) + "\n").encode())
            _atomic_write(folder / "checkpoint.json", json.dumps(checkpoint, ensure_ascii=False, indent=2).encode())
            db.execute("""UPDATE project_maintenance SET watermark=?,updated_unix=?,owner=NULL,lease_until=0,
                checkpoint_path=?,error_type=NULL WHERE project=? AND owner=?""",
                (watermark, time.time(), str(folder / "checkpoint.json"), project_id, owner))
        return {"state": "updated", "checkpoint": str(folder / "checkpoint.json")}
    except Exception as exc:
        if claimed:
            with connect() as db:
                db.execute("UPDATE project_maintenance SET owner=NULL,lease_until=?,error_type=? WHERE project=? AND owner=?",
                           (time.time() + 30, type(exc).__name__, project_id, owner))
        return {"state": "failed", "error_type": type(exc).__name__}


def maintenance_status(project_id):
    with connect() as db:
        _schema(db)
        row = db.execute("SELECT * FROM project_maintenance WHERE project=?", (project_id,)).fetchone()
    if not row:
        return {"project_id": project_id, "state": "not_yet_recorded"}
    return {"project_id": project_id, "state": "failed" if row["error_type"] else
            "updating" if row["lease_until"] > time.time() else "ready" if row["checkpoint_path"] else "pending",
            "event_watermark": row["watermark"], "updated_unix": row["updated_unix"],
            "checkpoint": row["checkpoint_path"], "error_type": row["error_type"],
            "retry_after_unix": row["lease_until"] if row["error_type"] else None}


def _after_call(name, bound, error_type):
    project_id = bound.arguments.get("project_id")
    projects = {project_id} if isinstance(project_id, str) and project_id else set()
    if not projects:
        job_ids = [value for key, value in bound.arguments.items()
                   if key in ("job_id", "baseline_job_id", "candidate_job_id") and isinstance(value, str)]
        if not job_ids:
            return
        with connect() as db:
            for job_id in job_ids:
                row = db.execute("""SELECT project FROM events WHERE kind='job_submitted'
                    AND json_extract(payload, '$.job_id')=? ORDER BY rowid DESC LIMIT 1""", (job_id,)).fetchone()
                if row:
                    projects.add(row[0])
    for project in projects:
        result = maintain_project(project, name, error_type, name in _CRITICAL)
        if result["state"] == "failed":
            _diagnose(f"ai2research maintenance failed: {result['error_type']}; inspect maintenance_status")


def instrument_service(namespace):
    """Keep tool signatures/async semantics while attaching one outer-call maintenance pass."""
    def decorate(function):
        signature = inspect.signature(function)

        def finish(token, bound, failure):
            _DEPTH.reset(token)
            if _DEPTH.get() == 0:
                try:
                    _after_call(function.__name__, bound, failure)
                except Exception as exc:
                    _diagnose(f"ai2research maintenance unavailable: {type(exc).__name__}")

        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def wrapped(*args, **kwargs):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                token, failure = _DEPTH.set(_DEPTH.get() + 1), ""
                try:
                    return await function(*args, **kwargs)
                except BaseException as exc:
                    failure = type(exc).__name__
                    raise
                finally:
                    finish(token, bound, failure)
        else:
            @wraps(function)
            def wrapped(*args, **kwargs):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                token, failure = _DEPTH.set(_DEPTH.get() + 1), ""
                try:
                    return function(*args, **kwargs)
                except BaseException as exc:
                    failure = type(exc).__name__
                    raise
                finally:
                    finish(token, bound, failure)
        return wrapped

    for name, function in list(namespace.items()):
        if name != "maintenance_status" and not name.startswith("_") and callable(function) and function.__module__ == namespace["__name__"]:
            namespace[name] = decorate(function)
