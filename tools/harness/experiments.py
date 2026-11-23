"""Proposal snapshots, bounded approvals, GPU submissions and result collection."""
from __future__ import annotations
import base64
import hashlib
import json
import math
import re
import sys
import uuid
from pathlib import Path, PurePosixPath
from .state import ROOT, config, connect, local_dir, now, record, require_active_project


def event(event_id, kind=None):
    with connect() as db:
        row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row is None or (kind and row["kind"] != kind):
        raise ValueError("No matching " + str(kind) + " record")
    return {**json.loads(row["payload"]), "event_id": row["id"], "project_id": row["project"]}


def canonical_digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prepare_experiment(project_id: str, title: str, script_path: str, parameters: dict | None = None,
                       input_files: list[str] | None = None, gpu_memory_mb: int = 2048,
                       host_memory_mb: int = 8192, timeout_seconds: int = 600,
                       wait_seconds: int = 120, remote_python: str = "python3",
                       evaluation_files: list[str] | None = None, parent_proposal_id: str = "",
                       evaluator_path: str = "", evaluation_on_gpu: bool = False, purpose: str = "research",
                       disable_transparent_hugepages: bool = False) -> dict:
    """Freeze a CPU/GPU experiment and optional process-local THP policy; never execute it."""
    require_active_project(project_id)
    if type(disable_transparent_hugepages) is not bool:
        raise ValueError("disable_transparent_hugepages must be a boolean")
    if not title.strip() or not re.fullmatch(r"[\w.-]{1,80}", project_id) or project_id in (".", ".."):
        raise ValueError("Title and project_id are required")
    if not (gpu_memory_mb == 0 or 256 <= gpu_memory_mb <= 22000) or not 512 <= host_memory_mb <= 65536 or not 1 <= timeout_seconds <= 86400 or not 0 <= wait_seconds <= 3600:
        raise ValueError("Invalid memory/time bounds")
    if gpu_memory_mb == 0 and evaluation_on_gpu:
        raise ValueError("CPU proposals cannot request GPU evaluation")
    if remote_python != "python3" and not (remote_python.startswith(str(PurePosixPath(config()["remote"]["allowed_home"])) + "/") and ".." not in PurePosixPath(remote_python).parts):
        raise ValueError("Interpreter must be python3 or an existing path within the configured user home")
    path = Path(script_path).resolve(strict=True)
    script = path.read_text(encoding="utf-8")
    files, total = [], len(script.encode())
    if len(input_files or []) > 100:
        raise ValueError("Too many input files")
    for name in input_files or []:
        relative = Path(name)
        target = path.parent / relative
        if relative.is_absolute() or ".." in relative.parts or target.is_symlink() or target.resolve() != target.absolute():
            raise ValueError("Input files must be relative regular files beside script")
        if name in ("script.py", "config.json", "worker.py", "spec.json", "status.json", "metrics.json", "run.log"):
            raise ValueError("Reserved input filename")
        raw = target.read_bytes()
        total += len(raw)
        files.append({"path": relative.as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()})
    if total > 10 * 1024 * 1024:
        raise ValueError("Initial code bundle is limited to 10 MiB; stage datasets separately")
    evaluation_files = sorted(evaluation_files or [])
    if any(name not in [item["path"] for item in files] for name in evaluation_files):
        raise ValueError("Evaluation files must be included in the input bundle")
    if not evaluator_path and len(evaluation_files) == 1 and evaluation_files[0].endswith(".py"):
        evaluator_path = evaluation_files[0]
    if evaluator_path and (evaluator_path not in evaluation_files or not evaluator_path.endswith(".py")):
        raise ValueError("Evaluator entry must be a frozen Python evaluation file")
    if purpose not in ("research", "toolchain_test"):
        raise ValueError("Purpose must be research or toolchain_test")
    if parent_proposal_id and event(parent_proposal_id, "experiment_proposal")["project_id"] != project_id:
        raise ValueError("Parent proposal belongs to another direction")
    cfg = config().get("gpu", {})
    manifest = {"title": title, "script": script, "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
                "files": files, "parameters": parameters or {}, "evaluation_files": evaluation_files,
                "parent_proposal_id": parent_proposal_id, "purpose": purpose, "resources": {"python": remote_python,
                "evaluator": evaluator_path, "evaluation_on_gpu": evaluation_on_gpu,
                "timeout_seconds": timeout_seconds, "memory_mb": host_memory_mb,
                "gpu": {"memory_mb": gpu_memory_mb, "reserve_mb": cfg.get("reserve_memory_mb", 1024),
                        "max_utilization_percent": cfg.get("max_utilization_percent", 10), "wait_seconds": wait_seconds,
                        "allowed_indices": cfg.get("allowed_indices", [0, 1])} if gpu_memory_mb else None}}
    if disable_transparent_hugepages:
        manifest["resources"]["disable_transparent_hugepages"] = True
    digest = canonical_digest(manifest)
    folder = local_dir() / "proposals" / digest
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result = record("experiment_proposal", {"title": title, "manifest_sha256": digest, "manifest_path": str(folder / "manifest.json"),
                                           "script_sha256": manifest["script_sha256"], "purpose": purpose, "resources": manifest["resources"]}, project_id)
    return {**result, "proposal_id": result["event_id"], "status": "awaiting_user_selection_and_approval"}


def approve_proposal(proposal_id, user_approval_text, max_runs=1, allow_code_iterations=False, mutable_parameters=None):
    """CLI helper only, after explicit user approval. Never exposed as an MCP tool."""
    proposal = event(proposal_id, "experiment_proposal")
    if not str(user_approval_text).strip() or type(max_runs) is not int or not 1 <= max_runs <= 100:
        raise ValueError("An explicit user approval quote and bounded max_runs are required")
    manifest = json.loads(Path(proposal["manifest_path"]).read_text(encoding="utf-8"))
    if canonical_digest(manifest) != proposal["manifest_sha256"]:
        raise ValueError("Proposal changed after review")
    if allow_code_iterations and not manifest.get("resources", {}).get("evaluator"):
        raise ValueError("Code iteration approval requires a separately executed frozen evaluator")
    return record("experiment_approval", {"proposal_id": proposal_id, "manifest_sha256": proposal["manifest_sha256"],
                                          "max_runs": max_runs, "user_approval_text": user_approval_text,
                                          "purpose": manifest.get("purpose", "research"),
                                          "allow_code_iterations": bool(allow_code_iterations),
                                          "mutable_parameters": mutable_parameters or []}, proposal["project_id"])


def submit_proposal(proposal_id: str, approval_id: str, *, cycle_id: str = "") -> dict:
    """Submit the immutable, user-approved GPU proposal; consumes one bounded run slot."""
    from .service import _remote
    proposal = event(proposal_id, "experiment_proposal")
    approval = event(approval_id, "experiment_approval")
    if approval["project_id"] != proposal["project_id"]:
        raise ValueError("Approval does not match this experiment")
    manifest = json.loads(Path(proposal["manifest_path"]).read_text(encoding="utf-8"))
    if canonical_digest(manifest) != proposal["manifest_sha256"]:
        raise ValueError("Proposal changed after review; prepare a new proposal")
    if approval.get("proposal_id") != proposal_id:
        if not approval.get("allow_code_iterations") or manifest.get("parent_proposal_id") != approval["proposal_id"]:
            raise ValueError("Approval does not cover this code variant")
        parent = event(approval["proposal_id"], "experiment_proposal")
        original = json.loads(Path(parent["manifest_path"]).read_text(encoding="utf-8"))
        if canonical_digest(original) != approval["manifest_sha256"]:
            raise ValueError("Original approved plan changed")
        original_files = {f["path"]: f["sha256"] for f in original["files"]}
        variant_files = {f["path"]: f["sha256"] for f in manifest["files"]}
        fixed = original["evaluation_files"]
        if manifest.get("purpose") != original.get("purpose") or manifest["resources"] != original["resources"] or manifest["evaluation_files"] != fixed or set(original_files) != set(variant_files) or any(original_files[n] != variant_files[n] for n in fixed):
            raise ValueError("Variant changes approved resources, input scope or frozen evaluation")
        sentinel = object()
        keys = set(original["parameters"]) | set(manifest["parameters"])
        changed = {k for k in keys if original["parameters"].get(k, sentinel) != manifest["parameters"].get(k, sentinel)}
        if not changed.issubset(set(approval.get("mutable_parameters", []))):
            raise ValueError("Variant changes parameters outside the approved search scope")
    elif approval.get("manifest_sha256") != proposal["manifest_sha256"]:
        raise ValueError("Approval does not match this experiment digest")
    job_id = cycle_job_id(cycle_id, proposal_id) if cycle_id else uuid.uuid4().hex
    research_kind = "approved_gpu_experiment" if manifest['resources'].get('gpu') else "approved_cpu_experiment"
    spec = {"kind": "synthetic_workflow_test" if manifest.get("purpose") == "toolchain_test" else research_kind, **manifest["resources"], "proposal_id": proposal_id,
            "approval_id": approval_id, "manifest_sha256": proposal["manifest_sha256"], "script_sha256": manifest["script_sha256"],
            "input_sha256": {f["path"]: f["sha256"] for f in manifest["files"]}, "config_sha256": canonical_digest(manifest["parameters"])}
    # Reserve and register before SSH: disconnects cannot orphan an unqueryable job.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        require_active_project(proposal['project_id'], db)
        rows = db.execute("SELECT payload FROM events WHERE kind='job_submitted'").fetchall()
        reserved = [json.loads(row[0]) for row in rows]
        reuse = cycle_id and any(row["job_id"] == job_id for row in reserved)
        if not reuse:
            used = sum(row.get("approval_id") == approval_id for row in reserved)
            if used >= approval["max_runs"]:
                raise ValueError("User-approved run count exhausted")
            payload = {"job_id": job_id, "approval_id": approval_id, "proposal_id": proposal_id, "state": "submission_reserved",
                       "spec": spec, "parameters": manifest["parameters"]}
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (uuid.uuid4().hex, proposal["project_id"], "job_submitted", now(), json.dumps(payload)))
    if reuse:
        from .recovery import retry_job_dispatch
        return retry_job_dispatch(job_id)
    try:
        result = _remote({"operation": "submit", "job_id": job_id, "spec": spec, "script": manifest["script"],
                          "config": manifest["parameters"], "files": manifest["files"]})
    except Exception as exc:
        # The worker may already have received the task. Keep its reserved ID
        # and budget slot; exception text can contain credentials or headers.
        record("job_dispatch_error", {"job_id": job_id, "proposal_id": proposal_id,
               "approval_id": approval_id, "state": "dispatch_uncertain", "failed_phase": "dispatch",
               "error_type": type(exc).__name__,
               "next_action": "Use retry_job_dispatch with this same job_id to confirm the frozen submission; do not submit a replacement or consume another run slot"}, proposal["project_id"])
        raise
    return record("job_dispatch", result, proposal["project_id"])


def cycle_job_id(cycle_id, proposal_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ai2research:cycle:{cycle_id}:{proposal_id}").hex


def submit_gpu_smoke(increment: int = 1) -> dict:
    """Fixed tiny GPU kernel acceptance, explicitly authorized as infrastructure testing."""
    from .service import _remote
    if increment not in (1, 2):
        raise ValueError("Only fixed smoke variants 1 and 2 are allowed")
    script = Path(__file__).with_name("cuda_smoke.py").read_text(encoding="utf-8")
    job_id = uuid.uuid4().hex
    spec = {"kind": "synthetic_gpu_toolchain_test", "python": "python3", "timeout_seconds": 30, "memory_mb": 2048,
            "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
            "gpu": {"memory_mb": 768, "reserve_mb": 2048, "max_utilization_percent": 10, "wait_seconds": 30, "allowed_indices": [0, 1]}}
    record("job_submitted", {"job_id": job_id, "state": "submission_reserved", "spec": spec, "parameters": {"increment": increment}}, "toolchain-gpu")
    result = _remote({"operation": "submit", "job_id": job_id, "spec": spec, "script": script, "config": {"increment": increment}})
    return record("job_dispatch", result, "toolchain-gpu")


def collect_job_results(job_id: str) -> dict:
    """Fetch bounded metrics/artifacts, verify SHA256 and persist an idempotent MLflow run."""
    from .service import _known_job, _remote
    registered = _known_job(job_id)
    result = _remote({"operation": "collect", "job_id": job_id})
    from .collection import validate_collection, publish_collection
    decoded, files = validate_collection(job_id, result, registered)
    result.pop("files")
    # Serialize collectors so concurrent status clients cannot create duplicate MLflow runs.
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT payload FROM events WHERE kind='job_collected'").fetchall()
        existing = next((json.loads(r[0]) for r in rows if json.loads(r[0]).get("job_id") == job_id), None)
        if existing:
            from .experience import record_job_experience
            experience = record_job_experience(db, registered['project_id'], job_id, existing['local_path'], existing)
            return {**existing, "experience": experience, "already_collected": True}
        folder = publish_collection(job_id, decoded)
        from mlflow import MlflowClient
        client = MlflowClient(tracking_uri="sqlite:///" + (local_dir() / "mlflow.sqlite3").as_posix())
        project = registered["project_id"]
        experiment_name = "gpu-toolchain-smoke" if project == "toolchain-gpu" else ("toolchain-" if result["status"].get("job_kind", "").startswith("synthetic") else "research-") + project
        experiment = client.get_experiment_by_name(experiment_name)
        experiment_id = experiment.experiment_id if experiment else client.create_experiment(experiment_name, artifact_location=(local_dir() / "mlflow-artifacts" / project).as_uri())
        tags = {"job_id": job_id, "evidence_kind": result["status"].get("job_kind", "cpu_toolchain_test"), "project_id": project,
                "job_state": result["status"]["state"]}
        # Recover a run if a prior collector died after MLflow write but before DB commit.
        recovered = client.search_runs([experiment_id], filter_string="tags.job_id = '" + job_id + "'", max_results=1)
        run = recovered[0] if recovered else client.create_run(experiment_id, tags=tags)
        spec = json.loads((folder / "spec.json").read_text())
        for key in ("script_sha256", "manifest_sha256", "approval_id", "proposal_id"):
            if key in spec:
                client.set_tag(run.info.run_id, key, str(spec[key]))
        for key, value in registered.get("parameters", {}).items():
            client.log_param(run.info.run_id, key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
        for key, value in result["metrics"].items():
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Non-finite returned metric")
            client.log_metric(run.info.run_id, key, value)
        for key in ("peak_gpu_memory_mb", "peak_host_rss_mb"):
            client.log_metric(run.info.run_id, key, result["status"].get(key, 0))
        client.log_artifacts(run.info.run_id, str(folder))
        succeeded = result["status"]["state"] == "completed" and not result["metrics_error"]
        client.set_terminated(run.info.run_id, status="FINISHED" if succeeded else "FAILED")
        summary = {**result, "local_path": str(folder), "files": files, "mlflow_run_id": run.info.run_id, "mlflow_experiment": experiment_name}
        from .experience import record_job_experience
        summary['experience'] = record_job_experience(db, project, job_id, folder, summary)
        (folder / "collection.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (uuid.uuid4().hex, project, "job_collected", now(), json.dumps(summary)))
    return summary


def compare_jobs(baseline_job_id: str, candidate_job_id: str, metric: str, direction: str = "minimize",
                 minimum_improvement: float = 0.0) -> dict:
    """Compare collected valid trials in one approved scope; record evidence for Codex's next iteration."""
    from .service import _known_job
    baseline = _known_job(baseline_job_id)
    candidate = _known_job(candidate_job_id)
    if baseline["project_id"] != candidate["project_id"]:
        raise ValueError("Compare trials within the same research direction")
    if direction not in ("minimize", "maximize") or not math.isfinite(minimum_improvement) or minimum_improvement < 0:
        raise ValueError("Invalid comparison direction or threshold")
    if baseline.get("approval_id") != candidate.get("approval_id"):
        raise ValueError("Trials belong to different approved evaluation scopes")
    with connect() as db:
        rows = db.execute("SELECT payload FROM events WHERE kind='job_collected'").fetchall()
    collected = {r["job_id"]: r for r in (json.loads(row[0]) for row in rows)}
    first, second = collected.get(baseline_job_id), collected.get(candidate_job_id)
    for trial in (first, second):
        if trial is None or trial["status"]["state"] != "completed" or trial.get("metrics_error") or metric not in trial["metrics"]:
            raise ValueError("Both jobs need successful, valid collected metrics")
    before, after = first["metrics"][metric], second["metrics"][metric]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (before, after)):
        raise ValueError("Comparison metrics must be finite")
    improvement = before - after if direction == "minimize" else after - before
    return record("trial_comparison", {"baseline_job_id": baseline_job_id, "candidate_job_id": candidate_job_id,
                  "metric": metric, "direction": direction, "baseline": before, "candidate": after,
                  "improvement": improvement, "minimum_improvement": minimum_improvement,
                  "decision": "improved" if improvement > minimum_improvement else "not_improved",
                  "baseline_run_id": first["mlflow_run_id"], "candidate_run_id": second["mlflow_run_id"],
                  "evidence_kind": first["status"].get("job_kind", "unknown")}, baseline["project_id"])
