"""Resume existing work without creating a second job or consuming another run slot."""
import json
import time
import uuid
from pathlib import Path
from .state import connect, record

ACTIVE = ("queued", "starting", "waiting_resources", "running", "evaluating")
_RESERVATION_SECONDS = 900


def _job_inventory(project_id, db=None):
    """Read job metadata across the full history without loading result bundles."""
    if db is None:
        with connect() as connection:
            return _job_inventory(project_id, connection)
    rows = db.execute("""SELECT json_extract(payload, '$.job_id') AS job_id,
            json_extract(payload, '$.proposal_id') AS proposal_id,
            json_extract(payload, '$.approval_id') AS approval_id,
            json_extract(payload, '$.state') AS state FROM events
            WHERE project=? AND kind='job_submitted' ORDER BY rowid DESC""", (project_id,)).fetchall()
    snapshots = db.execute("""SELECT kind, json_extract(payload, '$.job_id') AS job_id,
            COALESCE(json_extract(payload, '$.status.state'), json_extract(payload, '$.state')) AS state,
            json_extract(payload, '$.metrics_error') AS metrics_error FROM events
            WHERE project=? AND kind IN ('job_dispatch','job_status','job_collected') ORDER BY rowid""",
            (project_id,)).fetchall()
    latest = {}
    collected = {}
    for row in snapshots:
        if row["state"]:
            latest[row["job_id"]] = row["state"]
        if row["kind"] == "job_collected":
            collected[row["job_id"]] = bool(row["metrics_error"])
    return [{"job_id": row["job_id"], "proposal_id": row["proposal_id"], "approval_id": row["approval_id"],
             "state": latest.get(row["job_id"], row["state"] or "submitted"),
             "collected": row["job_id"] in collected,
             "metrics_error": collected.get(row["job_id"], False)} for row in rows]


def list_jobs(project_id="paper-research", limit=30, offset=0, pending_only=False):
    limit = max(1, min(int(limit), 100))
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    inventory = _job_inventory(project_id)
    pending = [job for job in inventory if not job["collected"] or job["state"] in ACTIVE]
    jobs = pending if pending_only else inventory
    end = offset + limit
    return {"project_id": project_id, "jobs": jobs[offset:end], "total": len(jobs),
            "pending_count": len(pending), "offset": offset,
            "next_offset": end if end < len(jobs) else None}


def retry_job_dispatch(job_id):
    from .service import _known_job, _remote
    from .experiments import event, canonical_digest
    registered = _known_job(job_id)
    from .state import require_active_project
    require_active_project(registered['project_id'])
    if not registered.get("proposal_id"):
        raise ValueError("Safe redispatch requires a frozen proposal")
    proposal = event(registered["proposal_id"], "experiment_proposal")
    manifest = json.loads(Path(proposal["manifest_path"]).read_text(encoding="utf-8"))
    if canonical_digest(manifest) != proposal["manifest_sha256"]:
        raise ValueError("Reserved proposal changed; do not redispatch")
    result = _remote({"operation": "submit", "job_id": job_id, "spec": registered["spec"], "script": manifest["script"],
                      "config": manifest["parameters"], "files": manifest["files"]})
    return record("job_dispatch_recovery", result, registered["project_id"])


def _reserve_resume_jobs(project_id, limit, offset):
    """Reserve one bounded page and advance a durable, stable submission cursor."""
    limit = max(1, min(int(limit), 100))
    if offset is not None and (type(offset) is not int or offset < 0):
        raise ValueError("offset must be a nonnegative integer or null")
    token = uuid.uuid4().hex
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS recovery_cursors (
            project_id TEXT PRIMARY KEY, submission_rowid INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS recovery_reservations (
            project_id TEXT NOT NULL, job_id TEXT NOT NULL, token TEXT NOT NULL,
            expires_unix REAL NOT NULL, PRIMARY KEY(project_id, job_id))""")
        db.execute("DELETE FROM recovery_reservations WHERE expires_unix<=?", (time.time(),))
        inventory = _job_inventory(project_id, db)
        pending = [job for job in inventory if not job["collected"] or job["state"] in ACTIVE]
        reserved = {row[0] for row in db.execute(
            "SELECT job_id FROM recovery_reservations WHERE project_id=?", (project_id,))}
        if offset is None:
            order = {row[0]: row[1] for row in db.execute("""SELECT
                json_extract(payload, '$.job_id'), rowid FROM events
                WHERE project=? AND kind='job_submitted' ORDER BY rowid""", (project_id,))}
            cursor = db.execute("SELECT submission_rowid FROM recovery_cursors WHERE project_id=?",
                                (project_id,)).fetchone()
            candidates = [job for job in pending if job["job_id"] not in reserved]
            if cursor:
                candidates.sort(key=lambda job: (order[job["job_id"]] >= cursor[0], -order[job["job_id"]]))
            jobs = candidates[:limit]
            if jobs:
                db.execute("INSERT OR REPLACE INTO recovery_cursors VALUES (?,?)",
                           (project_id, order[jobs[-1]["job_id"]]))
            next_offset = None
        else:
            jobs = [job for job in pending[offset:offset + limit] if job["job_id"] not in reserved]
            next_offset = offset + limit if offset + limit < len(pending) else None
        for job in jobs:
            db.execute("INSERT INTO recovery_reservations VALUES (?,?,?,?)",
                       (project_id, job["job_id"], token, time.time() + _RESERVATION_SECONDS))
    return jobs, token, {"total": len(pending), "next_offset": next_offset,
                         "has_more": len(pending) > len(jobs),
                         "pagination_mode": "rotation" if offset is None else "offset"}


def resume_project(project_id="paper-research", collect_finished=True, limit=30, offset=None):
    from .service import get_job_status, collect_job_results
    # Filter all historical submissions before paging, so recent completed jobs
    # cannot hide an older active job. Already collected jobs need no remote I/O.
    jobs, token, listing = _reserve_resume_jobs(project_id, limit, offset)
    recovered = []
    try:
        for job in jobs:
            # Keep the unprocessed batch reserved while bounded job I/O proceeds.
            with connect() as db:
                db.execute("UPDATE recovery_reservations SET expires_unix=? WHERE project_id=? AND token=?",
                           (time.time() + _RESERVATION_SECONDS, project_id, token))
                owned = db.execute("""SELECT 1 FROM recovery_reservations
                    WHERE project_id=? AND job_id=? AND token=?""",
                    (project_id, job["job_id"], token)).fetchone()
            try:
                if not owned:
                    raise RuntimeError("Recovery reservation expired and was reclaimed; leave this job to its current collector")
                status = get_job_status(job["job_id"])
                item = {"job_id": job["job_id"], "state": status["state"]}
                if status["state"] not in ACTIVE and collect_finished:
                    result = collect_job_results(job["job_id"])
                    item.update(mlflow_run_id=result["mlflow_run_id"], local_path=result["local_path"], metrics_error=result.get("metrics_error"))
                recovered.append(item)
            except Exception as exc:
                # One damaged or unreachable job must not prevent resuming other work.
                recovered.append({"job_id": job["job_id"], "state": "needs_attention", "error_type": type(exc).__name__,
                                  "message": str(exc)[:800], "next_action": "Inspect job evidence; never launch a replacement automatically"})
    finally:
        with connect() as db:
            db.execute("DELETE FROM recovery_reservations WHERE project_id=? AND token=?", (project_id, token))
    with connect() as db:
        approvals = db.execute("SELECT id,payload FROM events WHERE project=? AND kind='experiment_approval'", (project_id,)).fetchall()
        counts = db.execute("SELECT payload FROM events WHERE project=? AND kind='job_submitted'", (project_id,)).fetchall()
    budgets = []
    for row in approvals:
        approval = json.loads(row["payload"])
        used = sum(json.loads(x[0]).get("approval_id") == row["id"] for x in counts)
        budgets.append({"approval_id": row["id"], "max_runs": approval["max_runs"], "used": used, "remaining": max(0, approval["max_runs"] - used)})
    return record("project_resumed", {"project_id": project_id, "jobs": recovered, "run_budgets": budgets,
                  "pending_before": listing["total"], "next_offset": listing["next_offset"],
                  "has_more": listing["has_more"], "pagination_mode": listing["pagination_mode"],
                  "pagination_note": "Omit offset for durable rotation; explicit offsets refer to the current pending list and may shift after collection."}, project_id)
