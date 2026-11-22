"""Small, read-only views over existing records; no polling or model calls."""
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from .state import connect, execution_state, local_dir


def compact_events(history):
    fields = {"event_id", "kind", "created_utc", "project_id", "job_id", "task_id", "packet_id",
              "proposal_id", "approval_id", "request_id", "state", "status", "text", "summary",
              "tool", "error_type", "failed_phase", "next_action", "actual_model", "requested_model",
              "provider", "usage", "metrics_error", "local_path", "reason", "execution_state"}
    output = []
    for item in history:
        small = {key: value for key, value in item.items() if key in fields}
        omitted = sorted(set(item) - fields)
        # Nested status payloads may contain full resource/configuration details.
        if isinstance(small.get("status"), dict):
            small["status"] = {key: value for key, value in small["status"].items()
                               if key in ("state", "job_id", "exit_code", "metrics_authority")}
            omitted.append("status_details")
        for key, value in list(small.items()):
            if isinstance(value, str) and len(value) > 1200:
                small[key] = value[:1200]
                omitted.append(key + "_remainder")
        if omitted:
            small["omitted_fields"] = omitted
            small["detail_note"] = "Original record is persisted; use include_details=true when needed"
        output.append(small)
    return output


def compact_workflow(board):
    """Preserve dispatch decisions and references while omitting bulk evidence listings."""
    result = dict(board)
    omitted = []
    if "artifacts" in result:
        result["artifact_count"] = len(result.pop("artifacts"))
        omitted.append("artifacts")
    if "manifest" in result:
        result.pop("manifest")
        omitted.append("manifest")
    for key in ("tasks", "ready_tasks", "failed_tasks"):
        if key in result:
            def compact_task(task):
                small = {field: value for field, value in task.items()
                         if field not in ("instructions", "lease_token")}
                if len(small.get("summary", "")) > 600:
                    small["summary"] = small["summary"][:600]
                    small["summary_truncated"] = True
                return small
            if isinstance(result[key], dict):
                result[key] = {name: compact_task(task) for name, task in result[key].items()}
            else:
                result[key] = [compact_task(task) for task in result[key]]
    if "review_gates" in result:
        fields = {"packet_id", "stage", "state", "missing_reviewers", "stale_artifact_ids",
                  "requires_revision", "gate_blockers"}
        result["review_gates"] = {stage: {key: value for key, value in gate.items() if key in fields}
                                  for stage, gate in result["review_gates"].items()}
    result["detail_note"] = "Dispatch decisions were validated; use include_details=true for complete evidence metadata and task instructions"
    result["omitted_fields"] = omitted + ["task_instructions", "review_bodies"]
    return result


_ACTIONS = {
    "decision": ("决策", "记录决定"), "workflow_selection": ("选题", "选择候选"),
    "workflow_task_created": ("任务", "创建"), "workflow_task_claimed": ("任务", "领取"),
    "workflow_task_updated": ("任务", "更新"), "job_submitted": ("实验", "登记"),
    "workflow_task_evidence_repaired": ("任务", "修订证据"),
    "job_dispatch": ("实验", "提交"), "job_status": ("实验", "状态"),
    "job_dispatch_error": ("实验", "派发回复失败"),
    "job_collected": ("实验", "回收"), "job_evidence_attached": ("证据", "归档"),
    "job_experience": ("诊断", "保存观察"),
    "review_result": ("评审", "收到结果"), "review_error": ("评审", "调用失败"),
    "external_packet_review": ("评审", "证据包审查"), "packet_review": ("评审", "登记意见"),
    "project_execution_control": ("项目", "执行状态"),
}


def process_log(project_id, limit=20, after_event_id=""):
    """Return latest lines, or the next page after a cursor, in chronological order."""
    limit = max(1, min(int(limit), 100))
    with connect() as db:
        cursor = None
        if after_event_id:
            cursor = db.execute("SELECT rowid FROM events WHERE id=? AND project=?",
                                (after_event_id, project_id)).fetchone()
            if cursor is None:
                raise ValueError("Log cursor does not belong to this project")
        placeholders = ",".join("?" for _ in _ACTIONS)
        condition = " AND rowid>?" if cursor else ""
        direction = "ASC" if cursor else "DESC"
        params = [project_id, *_ACTIONS, *([cursor[0]] if cursor else []), limit + 1]
        rows = db.execute(f"""SELECT id, kind, created_utc,
            json_extract(payload, '$.job_id') AS job_id,
            json_extract(payload, '$.task_id') AS task_id,
            COALESCE(json_extract(payload, '$.status.state'), json_extract(payload, '$.state'),
                     json_extract(payload, '$.execution_state'), '') AS state,
            substr(json_extract(payload, '$.parameters.method'), 1, 100) AS method,
            substr(json_extract(payload, '$.parameters.dataset'), 1, 100) AS dataset,
            json_extract(payload, '$.parameters.seed') AS seed,
            substr(COALESCE(json_extract(payload, '$.next_action'), ''), 1, 180) AS next_action,
            substr(COALESCE(NULLIF(json_extract(payload, '$.summary'), ''),
                json_extract(payload, '$.text'), json_extract(payload, '$.reason'),
                json_extract(payload, '$.error_type'), json_extract(payload, '$.title'),
                json_extract(payload, '$.observation_codes'), ''), 1, 240) AS message
            FROM events WHERE project=? AND kind IN ({placeholders}){condition}
            ORDER BY rowid {direction} LIMIT ?""", params).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    if not cursor:
        rows.reverse()
    entries, lines = [], []
    for row in rows:
        stage, action = _ACTIONS[row["kind"]]
        stamp = datetime.fromisoformat(row["created_utc"]).astimezone(ZoneInfo("Asia/Shanghai"))
        message = " ".join(row["message"].split())
        if not message:
            labels = [row["method"], row["dataset"], f"seed={row['seed']}" if row["seed"] is not None else ""]
            message = " ".join(" ".join(value.split()) for value in labels if value)
        next_action = " ".join(row["next_action"].split())
        entry = {"event_id": row["id"], "time": stamp.isoformat(timespec="seconds"),
                 "stage": stage, "action": action, "state": row["state"], "message": message,
                 "job_id": row["job_id"], "task_id": row["task_id"], "next_action": next_action}
        entries.append(entry)
        identity = row["job_id"] or row["task_id"] or ""
        lines.append("｜".join(filter(None, [stamp.strftime("%m-%d %H:%M:%S"), stage, action,
                                             row["state"], identity[:8], message,
                                             "下一步：" + next_action if next_action else ""])))
    return {"project_id": project_id, "entries": entries, "lines": lines,
            "next_event_id": entries[-1]["event_id"] if entries else after_event_id,
            "has_more": more if cursor else False, "older_entries_omitted": more if not cursor else False,
            "source": "persisted events; no remote polling"}


def project_overview(project_id):
    """Inspect counts and a few work items without reading or validating artifact bytes."""
    from .workspace import _db, _project
    from .recovery import _job_inventory, ACTIVE
    with _db() as db:
        manifest = _project(db, project_id)
        control = execution_state(db, project_id)
        counts = {row[0]: row[1] for row in db.execute("""SELECT json_extract(payload, '$.state'), COUNT(*)
            FROM workflow_tasks WHERE project_id=? GROUP BY json_extract(payload, '$.state')""", (project_id,))}
        tasks = db.execute("""SELECT task_id, json_extract(payload, '$.title') AS title,
            json_extract(payload, '$.state') AS state, json_extract(payload, '$.role') AS role,
            json_extract(payload, '$.prompt_path') AS prompt_path,
            substr(json_extract(payload, '$.summary'), 1, 240) AS summary FROM workflow_tasks
            WHERE project_id=? AND json_extract(payload, '$.state')!='completed'
            ORDER BY CASE json_extract(payload, '$.state') WHEN 'failed' THEN 0 WHEN 'running' THEN 1 ELSE 2 END, rowid
            LIMIT 10""", (project_id,)).fetchall()
        artifacts = db.execute("""SELECT COUNT(*), COUNT(DISTINCT json_extract(payload, '$.path'))
            FROM workflow_artifacts WHERE project_id=?""", (project_id,)).fetchone()
        usage = db.execute("""SELECT COUNT(*), COALESCE(SUM(json_extract(payload, '$.usage.prompt_tokens')),0),
            COALESCE(SUM(json_extract(payload, '$.usage.completion_tokens')),0)
            FROM events WHERE project=? AND kind='review_result'""", (project_id,)).fetchone()
    jobs = _job_inventory(project_id)
    pending = [job for job in jobs if not job["collected"] or job["state"] in ACTIVE]
    return {"project_id": project_id, "title": manifest.get("title", ""),
            "execution_state": control["execution_state"], "task_counts": counts,
            "open_tasks": [dict(row) for row in tasks], "open_tasks_total": sum(v for k, v in counts.items() if k != "completed"),
            "artifact_count": artifacts[0], "artifact_paths": artifacts[1],
            "job_counts": dict(Counter(job["state"] for job in jobs)), "jobs_total": len(jobs),
            "pending_jobs": pending[:10], "pending_jobs_total": len(pending),
            "external_review_usage": {"calls": usage[0], "input_tokens": usage[1], "output_tokens": usage[2],
                                      "scope": "external review receipts only; excludes Codex subscription usage"},
            "recent_log": process_log(project_id, 5)["lines"], "state_store": str(local_dir()),
            "validation": "Recorded state only; use project_status/review_status to revalidate evidence and task dependencies"}
