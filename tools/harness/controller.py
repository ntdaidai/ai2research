"""Actions for a live research controller; durable tasks, never an autonomous daemon."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from .state import connect, now


def _history(project_id, kind):
    with connect() as db:
        rows = db.execute("SELECT id,payload FROM events WHERE project=? AND kind=? ORDER BY rowid",
                          (project_id, kind)).fetchall()
    return [{**json.loads(r["payload"]), "event_id": r["id"]} for r in rows]


def _task_id(task):
    return task.get("task_id", task.get("id"))


def select_candidate(project_id: str, task_id: str, reason: str) -> dict:
    """Select a completed paper-screening result and scaffold parallel experiment work."""
    from .workspace import project_status, create_task
    board = project_status(project_id)
    if board.get('execution_state') == 'paused':
        raise ValueError('Project is paused; candidate selection requires explicit resume')
    tasks = board["tasks"]
    chosen = next((t for t in tasks if _task_id(t) == task_id), None)
    if not chosen or chosen["state"] != "completed" or chosen["role"] != "screening":
        raise ValueError("Select a completed screening task from this project")
    if chosen.get("artifacts_valid") is not True:
        raise ValueError("Selected screening evidence is missing or changed")
    if not reason.strip():
        raise ValueError("Record the evidence-based selection reason")
    payload = {
        "task_id": task_id, "reason": reason, "artifact_ids": chosen["artifact_ids"],
        "artifact_sha256": {a["artifact_id"]: a["sha256"] for a in board["artifacts"] if a["artifact_id"] in chosen["artifact_ids"]},
    }
    # Select once across concurrent controllers. Creating the dependent DAG is
    # idempotent, so an interrupted winner can continue with the same selection.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id,payload FROM events WHERE project=? AND kind='workflow_selection' ORDER BY rowid",
                          (project_id,)).fetchall()
        if rows:
            previous = json.loads(rows[-1]["payload"])
            if previous != payload:
                raise ValueError("This project already has a different frozen selection; use a separate project for another candidate")
            selection = {**previous, "event_id": rows[-1]["id"], "kind": "workflow_selection"}
        else:
            identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"ai2research:selection:{project_id}").hex
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                       (identifier, project_id, "workflow_selection", now(), json.dumps(payload)))
            selection = {**payload, "event_id": identifier, "kind": "workflow_selection"}
    created = {}

    def task(key, title, role, dependencies, instructions, outputs):
        result = create_task(project_id, title, role, dependencies=dependencies, instructions=instructions,
                             output_paths=outputs, task_key="selected-" + key)
        created[key] = result
        return _task_id(result)

    method = task("method", "Formulate the falsifiable method and search plan", "method_designer", [task_id],
                  "Read the selected screening artifacts and nearest work. Define mechanism, baseline, mutable method files, "
                  "search budget proposal and stop conditions. Use creative-thinking-for-research as useful. "
                  "Prepare a concrete reviewable plan; no experiment approval is implied.", ["research/method_plan.md"])
    baseline = task("baseline", "Adapt the official baseline and isolated environment", "implementation", [task_id],
                    "Pin official code and dependencies. Prepare executable baseline adapter, data preparation and run commands. "
                    "Use a task-specific code directory. Measure actual cost only within user-approved experiments.",
                    ["experiments/baseline_adapter.py", "experiments/environment.md"])
    evaluator = task("evaluator", "Design independent fixed evaluation", "evaluation", [task_id],
                     "Define metrics, fixed data partitions, baseline comparability and independent evaluator. "
                     "Reserve confirmation data/seeds for verification. Keep evaluator independent of method implementation.",
                     ["experiments/evaluator.py", "experiments/evaluation_protocol.md"])
    search = task("search", "Run bounded method search with the existing worker", "experiment_runner", [method, baseline, evaluator],
                  "Prepare actual frozen baseline/candidate proposals, obtain the required experiment scope, then use "
                  "research_cycle and existing worker. Iterate on method, inspect real logs, keep/discard and record failures. "
                  "Attach collected job evidence with attach_job_evidence; never invent metrics or approvals.",
                  ["results/search_summary.md"])
    task("verification", "Independently reproduce the selected improvement", "verification", [search],
         "Inspect fixed protocol and collected jobs; run approved confirmation seeds/workloads, check leakage, fairness and "
         "uncertainty. Record exact job IDs and negative results. Attach measured evidence; report limitations directly.",
         ["results/verification.md"])
    task("ablation", "Isolate the contribution with controlled ablations", "ablation", [search],
         "Design minimal component-removal and matched-budget controls. Use actual approved experiments and attach collected "
         "job evidence. Relate component effects to the hypothesized mechanism; list untested claims.",
         ["results/ablation.md"])
    return {"selection": selection, "tasks": created, "experiment_approval": "required before execution"}


def attach_job_evidence(project_id: str, job_id: str, phase: str = "search") -> dict:
    """Copy an already collected, verified job into the project's evidence registry."""
    from .experiments import event
    from .workspace import project_root, project_status, register_artifact
    if phase not in ("baseline", "search", "verification", "ablation", "rebuttal"):
        raise ValueError("Use baseline, search, verification, ablation or rebuttal")
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("Invalid job ID")
    with connect() as db:
        rows = db.execute("SELECT payload FROM events WHERE project=? AND kind='job_collected' ORDER BY rowid",
                          (project_id,)).fetchall()
    saved = next((json.loads(row[0]) for row in reversed(rows) if json.loads(row[0]).get("job_id") == job_id), None)
    if not saved or saved.get("metrics_error") or saved.get("status", {}).get("state") != "completed":
        raise ValueError("Attach a successfully collected job belonging to this project")
    status = saved["status"]
    folder = Path(saved["local_path"]).resolve(strict=True)
    spec = json.loads((folder / "spec.json").read_text())
    synthetic = spec.get("kind", "").startswith("synthetic") or spec.get("kind", "").startswith("toolchain")
    if not synthetic and (not spec.get("evaluator") or status.get("metrics_authority") != spec["evaluator"]):
        raise ValueError("Research evidence must come from the fixed independent evaluator")
    if not synthetic:
        from .evidence import _job_records, _validate_job
        _validate_job(project_id, job_id, *_job_records(project_id))
    if spec.get("proposal_id"):
        proposal = event(spec["proposal_id"], "experiment_proposal")
        if proposal["project_id"] != project_id:
            raise ValueError("Collected proposal belongs to another project")
    root = project_root(project_id)
    synthetic = synthetic or project_status(project_id).get("evidence_kind") == "toolchain_test"
    destination = root / "results" / phase / job_id
    if destination.resolve() != destination:
        raise ValueError("Evidence directory contains a symlink")
    verified = []
    for item in saved.get("files", []):
        name = item["path"]
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe collected path")
        source = folder / relative
        if source.is_symlink() or source.resolve() != source or not source.is_file():
            raise ValueError("Collected file is not regular")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Collected evidence changed: " + name)
        target = destination / relative
        if target.resolve() != target:
            raise ValueError("Unsafe destination")
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("Previously attached evidence changed")
        if name == "metrics.json":
            kind = {"verification": "verification_results", "ablation": "ablation_results"}.get(phase, "experiment_result")
        elif relative.suffix == ".py":
            kind = "source_code"
        elif relative.name.startswith("requirements") or relative.name in ("uv.lock", "poetry.lock", "environment.yml"):
            kind = "dependency_lock"
        else:
            kind = "experiment_artifact"
        verified.append((target, raw, kind))
    if not verified:
        raise ValueError("No collected files available")
    # No file or evidence record is published until every source and existing
    # destination has passed validation. Interrupted copies can be resumed.
    registered = []
    empty_files = []
    for target, raw, kind in verified:
        target.parent.mkdir(parents=True, exist_ok=True)
        _publish_snapshot(target, raw)
        if not raw:
            empty_files.append(target.relative_to(root).as_posix())
            continue
        registered.append(register_artifact(project_id, target.relative_to(root).as_posix(), kind,
                                            "toolchain_test" if synthetic else "research", job_id))
    attachment = {"job_id": job_id, "phase": phase, "artifact_ids": [a["artifact_id"] for a in registered],
                  "evidence_kind": "toolchain_test" if synthetic else "research", "copied_empty_files": empty_files}
    attachment_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ai2research:attachment:{project_id}:{job_id}:{phase}").hex
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                   (attachment_id, project_id, "job_evidence_attached", now(), json.dumps(attachment)))
    return {"job_id": job_id, "phase": phase, "evidence_kind": "toolchain_test" if synthetic else "research",
            "artifacts": registered, "copied_empty_files": empty_files}


def _publish_snapshot(target: Path, raw: bytes) -> None:
    """Publish a complete file without replacing an existing evidence snapshot."""
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError("Previously attached evidence changed")
        return
    with tempfile.TemporaryDirectory(prefix=".attachment-", dir=target.parent) as temporary:
        stage = Path(temporary) / "snapshot"
        stage.write_bytes(raw)
        try:
            # Same-filesystem link creation is atomic and never exposes a
            # partially written destination. A competing identical import wins
            # harmlessly; differing existing data is preserved and rejected.
            os.link(stage, target)
        except FileExistsError:
            if target.read_bytes() != raw:
                raise ValueError("Previously attached evidence changed") from None


def research_workflow(action: str = "status", project_id: str = "paper-research",
                      seed_papers: list[str] | None = None, title: str = "",
                      task_id: str = "", reason: str = "") -> dict:
    """Start from seed papers, inspect ready parallel tasks, or select a research candidate."""
    from .workspace import start_project, project_status
    if action == "start":
        return start_project(project_id, seed_papers or [], title)
    if action == "select":
        return select_candidate(project_id, task_id, reason)
    if action not in ("status", "next"):
        raise ValueError("Use start, status, next or select")
    board = project_status(project_id)
    if board.get('execution_state') == 'paused':
        return {**board, 'ready_tasks': [], 'next_action': 'paused_by_user',
                'dispatch_mode': 'read/collect/archive only until explicit user resume'}
    tasks = board["tasks"]
    completed = {_task_id(t) for t in tasks if t["state"] == "completed" and t.get("artifacts_valid")}
    ready = [t for t in tasks if t.get("ready")]
    failed = [t for t in tasks if t["state"] == "failed"]
    invalid = [t["task_id"] for t in tasks if t["state"] == "completed" and not t.get("artifacts_valid")]
    selection = _history(project_id, "workflow_selection")
    from .evidence import list_review_packets, review_status
    packets = list_review_packets(project_id)
    latest = {}
    for packet in packets:
        latest.setdefault(packet["stage"], packet)
    gates = {stage: review_status(project_id, p["packet_id"]) for stage, p in latest.items()}
    if invalid:
        next_action = "repair_changed_task_evidence"
    elif not selection:
        if any(t["role"] == "literature_search" for t in ready):
            next_action = "delegate_literature_search"
        else:
            next_action = "delegate_screening" if ready else "select_candidate" if tasks and len(completed) == len(tasks) else "inspect_active_tasks"
    elif gates.get("manuscript", {}).get("state") == "ready":
        next_action = "audit_delivery_requirements_and_review_coverage"
    elif gates.get("experiment", {}).get("state") == "ready":
        next_action = "delegate_chapters" if not any(t["role"] == "chapter_writer" for t in tasks) else "finish_chapters_and_manuscript_review"
    else:
        next_action = "delegate_ready_tasks" if ready else "prepare_experiment_review" if all(t["state"] == "completed" for t in tasks) else "inspect_active_tasks"
    return {**board, "ready_tasks": ready, "failed_tasks": failed, "review_gates": gates,
            "invalid_task_ids": invalid,
            "next_action": next_action, "dispatch_mode": "host subagents via the active controller",
            "experiment_execution": "existing user-approved frozen proposal scope"}
