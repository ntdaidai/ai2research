from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .state import ROOT, config, connect, events, local_dir, now, record, reserve_review, review_limit, transport


def _tool_python(tool: str) -> Path:
    tools = config()["tools"]
    key = f"{tool}_python"
    return ROOT / (tools.get(f"{key}_posix", tools[key]) if os.name == "posix" else tools[key])


def get_project_state(project_id: str = "paper-research", limit: int = 50, include_details: bool = False) -> dict:
    """Read shared decisions, jobs and review records. Research approvals remain explicit."""
    history = events(project_id, limit)
    if not include_details:
        from .monitoring import compact_events
        history = compact_events(history)
    from .recovery import list_jobs
    jobs = list_jobs(project_id)
    return {"project_id": project_id, "state_store": str(local_dir()), "events": history, "jobs": jobs["jobs"],
            "jobs_total": jobs["total"], "pending_jobs_total": jobs["pending_count"], "jobs_next_offset": jobs["next_offset"],
            "project_record": str(ROOT / "RESEARCH_PLAN.md"), "mode": "Codex-driven research scaffold"}


def record_decision(text: str, project_id: str = "paper-research") -> dict:
    """Record a decision or next action. This does not approve research experiments."""
    if not text.strip() or len(text) > 20000:
        raise ValueError("Decision must contain 1–20000 characters")
    return record("decision", {"text": text}, project_id)


def start_project(project_id: str, seed_papers: list[str], title: str = "", evidence_kind: str = "research") -> dict:
    """Start a controller-led project from paper URLs, IDs or local paths; create parallel screening tasks."""
    from .workspace import start_project as run
    return run(project_id, seed_papers, title, evidence_kind)


def start_from_request(project_id: str, domain: str, requirements: str = "", paper_count: int = 5,
                       lookback_days: int = 365, as_of: str = "2026-11-18") -> dict:
    """Start from a field and requirements; delegate strict recent top-venue/open-code discovery."""
    from .discovery import start_from_request as run
    return run(project_id, domain, requirements, paper_count, lookback_days, as_of)


def record_paper_candidates(project_id: str, candidates: list[dict]) -> dict:
    """Register a fully verified recent top-venue/open-code shortlist and create independent reading tasks."""
    from .discovery import record_paper_candidates as run
    return run(project_id, candidates)


def project_status(project_id: str) -> dict:
    """Inspect all durable tasks and evidence of a paper project, without executing work."""
    from .workspace import project_status as run
    return run(project_id)


def project_overview(project_id: str) -> dict:
    """Read a small progress summary; no artifact reads, remote polling or model calls."""
    from .monitoring import project_overview as run
    return run(project_id)


def process_log(project_id: str, limit: int = 20, after_event_id: str = "") -> dict:
    """Read short chronological progress lines; pass next_event_id to read only new entries."""
    from .monitoring import process_log as run
    return run(project_id, limit, after_event_id)


def maintenance_status(project_id: str) -> dict:
    """Inspect automatic local handoff/cleanup status and its checkpoint path."""
    from .maintenance import maintenance_status as run
    return run(project_id)


def set_project_execution_state(project_id: str, state: str, user_instruction: str, reason: str = "") -> dict:
    """Pause or explicitly resume new tasks/experiments, preserving existing evidence and jobs."""
    from .workspace import set_project_execution_state as run
    return run(project_id, state, user_instruction, reason)


def research_workflow(action: str = "status", project_id: str = "paper-research", seed_papers: list[str] | None = None,
                      title: str = "", task_id: str = "", reason: str = "", include_details: bool = False) -> dict:
    """Main controller entry: start from papers, inspect next parallel work or select a screened candidate."""
    from .controller import research_workflow as run
    board = run(action, project_id, seed_papers, title, task_id, reason)
    if include_details:
        return board
    from .monitoring import compact_workflow
    return compact_workflow(board)


def create_task(project_id: str, title: str, role: str, dependencies: list[str] | None = None,
                instructions: str = "", output_paths: list[str] | None = None, task_key: str = "") -> dict:
    """Create a dependent task for an independently delegated agent, with idempotent keys."""
    from .workspace import create_task as run
    return run(project_id, title, role, dependencies, instructions, output_paths, task_key)


def claim_task(project_id: str, task_id: str, owner: str, lease_seconds: int = 900) -> dict:
    """Acquire a ready task atomically; return the token needed to update it."""
    from .workspace import claim_task as run
    return run(project_id, task_id, owner, lease_seconds)


def update_task(project_id: str, task_id: str, lease_token: str, state: str, summary: str = "",
                artifact_ids: list[str] | None = None) -> dict:
    """Update an owned task; completion checks declared artifact paths and checksums."""
    from .workspace import update_task as run
    return run(project_id, task_id, lease_token, state, summary, artifact_ids)


def repair_changed_task_evidence(project_id: str, task_id: str, artifact_ids: list[str], summary: str) -> dict:
    """Rebind only completed task outputs to verified current revisions; preserve old evidence and reviews."""
    from .workspace import repair_changed_task_evidence as run
    return run(project_id, task_id, artifact_ids, summary)


def register_artifact(project_id: str, path: str, kind: str, evidence_kind: str = "research", source_job_id: str = "") -> dict:
    """Register a regular file relative to the project's root with its content checksum."""
    from .workspace import register_artifact as run
    return run(project_id, path, kind, evidence_kind, source_job_id)


def attach_job_evidence(project_id: str, job_id: str, phase: str = "search") -> dict:
    """Import a collected experiment's verified artifacts into a project's evidence registry."""
    from .controller import attach_job_evidence as run
    return run(project_id, job_id, phase)


def prepare_review(project_id: str, stage: str, artifact_ids: list[str], rubric: dict | None = None, round_key: str = "") -> dict:
    """Freeze the same evidence version for independent agent and DeepSeek review; no API call."""
    from .evidence import prepare_review as run
    return run(project_id, stage, artifact_ids, rubric, round_key)


def record_review(project_id: str, packet_id: str, reviewer: str, review: dict, model: str = "", provider: str = "") -> dict:
    """Store an independent agent's structured review; external reviews require a real provider call."""
    from .evidence import record_review as run
    return run(project_id, packet_id, reviewer, review, model, provider)


def run_external_review(project_id: str, packet_id: str, max_output_tokens: int = 2048) -> dict:
    """Review a frozen packet with the configured DeepSeek model; avoid duplicate paid calls."""
    from .evidence import run_external_review as run
    return run(project_id, packet_id, max_output_tokens)


def review_status(project_id: str, packet_id: str, minimum_score: float = 7.0) -> dict:
    """Check two independent reviews against the same unchanged evidence version."""
    from .evidence import review_status as run
    return run(project_id, packet_id, minimum_score)


def create_revision_tasks(project_id: str, packet_id: str) -> dict:
    """Turn review issues into durable repair or rebuttal tasks without launching experiments."""
    from .evidence import create_revision_tasks as run
    return run(project_id, packet_id)


def build_experiment_matrix(project_id: str, baseline_job_id: str, candidate_job_ids: list[str],
                            metric: str, direction: str = "minimize") -> dict:
    """Build a measured comparison and verification/ablation checklist from collected jobs."""
    from .evidence import build_experiment_matrix as run
    return run(project_id, baseline_job_id, candidate_job_ids, metric, direction)


def publication_plan(project_id: str, venue_style: str = "generic") -> dict:
    """Plan separate chapter tasks with a shared outline, terminology and evidence ledger."""
    from .publication import publication_plan as run
    return run(project_id, venue_style)


def assemble_manuscript(project_id: str, compile_pdf: bool = True) -> dict:
    """Assemble completed chapters and compile a fresh PDF with the installed TeX adapter."""
    from .publication import assemble_manuscript as run
    return run(project_id, compile_pdf)


def export_results_figure(project_id: str, source_path: str, metric: str, title: str = "") -> dict:
    """Plot registered measured data and retain source hashes, vector output and a reproduction script."""
    from .publication import export_results_figure as run
    return run(project_id, source_path, metric, title)


def export_delivery(project_id: str, allow_incomplete: bool = False, artifact_ids: list[str] | None = None) -> dict:
    """Package the paper and evidence with a checksum manifest and explicit review/completeness status."""
    from .publication import export_delivery as run
    return run(project_id, allow_incomplete, artifact_ids)


def extract_paper_text(project_id: str, source_path: str) -> dict:
    """Extract local paper text with explicit pdftotext provenance; no implicit Docling substitution."""
    from .documents import extract_paper_text as run
    return run(project_id, source_path)


def export_project_diagram(project_id: str, source_path: str, output_format: str = "svg") -> dict:
    """Export and register editable project-local draw.io diagrams and rendered outputs."""
    from .publication import export_project_diagram as run
    return run(project_id, source_path, output_format)


def list_review_packets(project_id: str) -> list[dict]:
    """List review rounds newest first, with declared coverage and evidence hashes."""
    from .evidence import list_review_packets as run
    return run(project_id)


def validate_result_artifact(project_id: str, artifact_id: str) -> dict:
    """Verify that a result artifact comes from collected independent evaluation, not a label alone."""
    from .evidence import validate_result_artifact as run
    return run(project_id, artifact_id)


def list_projects(limit: int = 20) -> dict:
    """Find persisted paper projects before creating a new workspace."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1–100")
    with connect() as db:
        exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_projects'").fetchone()
        if not exists:
            return {"projects": []}
        rows = db.execute("SELECT manifest FROM workflow_projects ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
    return {"projects": [json.loads(r[0]) for r in rows], "state_store": str(local_dir())}


def doctor() -> dict:
    packages = {}
    for name in ("fastmcp", "litellm", "optuna", "mlflow-skinny", "matplotlib"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version.split()[0], "root": str(ROOT), "packages": packages,
            "job_transport": transport(), "state_store": str(local_dir()),
            "ssh": shutil.which("ssh"), "pdflatex": shutil.which("pdflatex"), "latexmk": shutil.which("latexmk"),
            "tectonic": shutil.which("tectonic") or (str(Path.home() / ".local/bin/tectonic") if (Path.home() / ".local/bin/tectonic").is_file() else None),
            "arxiv_python": str(_tool_python("arxiv")) if _tool_python("arxiv").is_file() else None,
            "credential_configured": bool(os.environ.get(config()["review"]["key_env"])) or
            (Path.home() / ".config/ai2research/review.key").is_file() or
            (sys.platform == "win32" and (local_dir() / "dashscope.dpapi").is_file()),
            "review_model": config()["review"]["model"], "review_call_limit": review_limit(),
            "review_budget": review_budget(),
            "research_execution": "GPU worker available; research requires user-approved frozen proposal"}


def review_budget() -> dict:
    """Read the shared review allowance without sending an API request."""
    with connect() as db:
        used = db.execute("SELECT COUNT(*) FROM events WHERE kind='review_request'").fetchone()[0]
    cfg = config()["review"]
    return {"used_calls": used, "max_calls": review_limit(),
            "remaining_calls": max(0, review_limit() - used),
            "max_output_tokens": cfg["max_output_tokens"], "model": cfg["model"],
            "provider": cfg["provider"], "api_base": cfg["api_base"]}


def toolchain_status() -> dict:
    """Inspect installed research skills and executable adapters without paid calls."""
    skills_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    names = ("ai2research", "brainstorming-research-ideas", "creative-thinking-for-research",
             "ml-paper-writing", "systems-paper-writing", "nature-polishing", "anti-defensive-writing", "drawio-skill")
    skills = {name: {"available": (skills_root / name / "SKILL.md").is_file(),
                     "path": str(skills_root / name / "SKILL.md")} for name in names}
    drawio = shutil.which("drawio")
    if not drawio and (Path.home() / ".local/bin/drawio").is_file():
        drawio = str(Path.home() / ".local/bin/drawio")
    return {"mode": "active-controller", "skills": skills, "drawio": drawio,
            "latex": doctor()["tectonic"] or shutil.which("pdflatex"),
            "experiment_adapter": "existing frozen proposal / worker / MLflow",
            "agent_adapter": "controller delegates to host subagents; tasks are durable, no daemon",
            "review_budget": review_budget(), "state_store": str(local_dir())}


async def research_cycle(action: str, project_id: str = "paper-research", cycle_id: str = "",
                         paper_query: str = "", code_query: str = "", evidence_id: str = "",
                         approval_id: str = "", candidate_proposal_ids: list[str] | None = None,
                         hypothesis: str = "", metric: str = "", direction: str = "minimize",
                         minimum_improvement: float = 0.0, target: float | None = None,
                         wait_seconds: int = 30) -> dict:
    """Unified entry for discovery, starting, adding proposals, inspecting and advancing a research cycle."""
    from .cycle import research_cycle as run
    return await run(action, project_id, cycle_id, paper_query, code_query, evidence_id, approval_id,
                     candidate_proposal_ids, hypothesis, metric, direction, minimum_improvement, target, wait_seconds)


def _remote(request):
    cfg = config()["remote"]
    # Until a separate home is explicitly configured in the worker, fail closed.
    if Path(cfg["allowed_home"]).name in ("", ".", "..") or Path(cfg["workspace"]) != Path(cfg["allowed_home"]) / ".research-harness":
        raise ValueError("Remote workspace differs from authorized the configured user home scope")
    worker_path = Path(__file__).with_name("remote_worker.py")
    worker = worker_path.read_bytes()
    payload = dict(request)
    if request["operation"] == "submit":
        payload["worker_source"] = base64.b64encode(worker).decode()
    mode = transport()
    stdin = json.dumps(payload).encode()
    if mode == "local":
        if sys.platform != "linux":
            raise ValueError("Local worker transport is supported on the Linux research server")
        import pwd
        account = pwd.getpwuid(os.geteuid())
        if account.pw_name != cfg["user"] or account.pw_dir != cfg["allowed_home"]:
            raise ValueError("Local worker must run under the authorized research account")
        argv = [cfg["python"], str(worker_path)]
    else:
        # Send source and request over stdin; code bundles exceed Windows argv limits.
        source = "import sys,base64;exec(base64.b64decode(sys.stdin.buffer.readline()))"
        command = shlex.join([cfg["python"], "-c", source])
        stdin = base64.b64encode(worker) + b"\n" + stdin
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(cfg["port"]),
                f'{cfg["user"]}@{cfg["host"]}', command]
    result = subprocess.run(argv, input=stdin, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError(f"{mode} worker failed: " + result.stdout.decode("utf-8", "replace")[-1500:] + result.stderr.decode("utf-8", "replace")[-1000:])
    return json.loads(result.stdout.decode("utf-8"))


def probe_server() -> dict:
    """Read user home, Python and GPU inventory without writing or allocating a GPU."""
    result = _remote({"operation": "probe"})
    return record("server_probe", result)


SMOKE = """import json, os, time
print(json.dumps({'kind': 'toolchain_cpu_smoke', 'gpu_visible': os.environ.get('CUDA_VISIBLE_DEVICES'), 'pid': os.getpid()}), flush=True)
time.sleep(3)
print(json.dumps({'status': 'ok', 'sum_squares': sum(i*i for i in range(1000))}), flush=True)
"""


def submit_smoke() -> dict:
    """Submit a fixed 3-second CPU-only connectivity test within the configured user workspace."""
    job_id = uuid.uuid4().hex
    result = _remote({"operation": "submit", "job_id": job_id, "script": SMOKE,
                      "spec": {"kind": "toolchain_cpu_smoke", "timeout_seconds": 15, "memory_mb": 256}})
    return record("job_submitted", {**result, "script_sha256": hashlib.sha256(SMOKE.encode()).hexdigest()})


def submit_experiment(project_id: str, script_path: str = "", approval_id: str = "", timeout_seconds: int = 60, proposal_id: str = "") -> dict:
    """Submit a frozen approved GPU proposal, or a legacy approved CPU script.

    For GPU use proposal_id/approval_id; all resources come from the frozen plan.
    """
    from .state import require_active_project
    require_active_project(project_id)
    if proposal_id:
        from .experiments import event, submit_proposal
        if event(proposal_id, "experiment_proposal")["project_id"] != project_id or script_path:
            raise ValueError("Project mismatch or attempted override of frozen script")
        return submit_proposal(proposal_id, approval_id)
    path = Path(script_path).resolve(strict=True)
    script = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(script.encode()).hexdigest()
    approvals = events(project_id, 500, "experiment_approval")
    approval = next((a for a in approvals if a["event_id"] == approval_id), None)
    if approval is None or approval.get("script_sha256") != digest or not 1 <= timeout_seconds <= approval.get("max_timeout_seconds", 0):
        raise ValueError("No matching user-approved experiment manifest; research execution refused")
    # Backward compatibility for old explicitly approved CPU scripts.
    job_id = uuid.uuid4().hex
    result = _remote({"operation": "submit", "job_id": job_id, "script": script,
                      "spec": {"kind": "approved_cpu_experiment", "timeout_seconds": timeout_seconds, "memory_mb": 512,
                               "script_sha256": digest, "approval_id": approval_id}})
    return record("job_submitted", {**result, "approval_id": approval_id, "script_sha256": digest}, project_id)


def _known_job(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid job ID")
    with connect() as db:
        rows = db.execute("SELECT project,payload FROM events WHERE kind='job_submitted'").fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        if payload.get("job_id") == job_id:
            return {**payload, "project_id": row["project"]}
    raise ValueError("Only locally registered harness jobs can be queried or cancelled")


def get_job_status(job_id: str) -> dict:
    """Query a registered framework job, including its exit status."""
    known = _known_job(job_id)
    return record("job_status", _remote({"operation": "status", "job_id": job_id}), known["project_id"])


def read_job_logs(job_id: str, tail_bytes: int = 12000) -> dict:
    """Read bounded logs of a registered framework job only."""
    _known_job(job_id)
    return _remote({"operation": "logs", "job_id": job_id, "tail_bytes": tail_bytes})


def cancel_job(job_id: str) -> dict:
    """Cancel only a registered framework supervisor, never arbitrary server processes."""
    known = _known_job(job_id)
    return record("job_cancel", _remote({"operation": "cancel", "job_id": job_id}), known["project_id"])


def prepare_experiment(project_id: str, title: str, script_path: str, parameters: dict | None = None,
                       input_files: list[str] | None = None, gpu_memory_mb: int = 2048,
                       host_memory_mb: int = 8192, timeout_seconds: int = 600,
                       wait_seconds: int = 120, remote_python: str = "python3",
                       evaluation_files: list[str] | None = None, parent_proposal_id: str = "",
                       evaluator_path: str = "", evaluation_on_gpu: bool = False, purpose: str = "research",
                       disable_transparent_hugepages: bool = False) -> dict:
    """Freeze an experiment; gpu_memory_mb=0 uses CPU. THP disable is an opt-in child-process resource policy."""
    from .experiments import prepare_experiment as prepare
    return prepare(project_id, title, script_path, parameters, input_files, gpu_memory_mb,
                   host_memory_mb, timeout_seconds, wait_seconds, remote_python, evaluation_files, parent_proposal_id,
                   evaluator_path, evaluation_on_gpu, purpose, disable_transparent_hugepages)


def submit_gpu_smoke(increment: int = 1) -> dict:
    """Run a fixed 4096-element CUDA kernel to validate the GPU toolchain, not research."""
    from .experiments import submit_gpu_smoke as submit
    return submit(increment)


def collect_job_results(job_id: str) -> dict:
    """Fetch verified metrics, logs, code/config and artifacts into local MLflow."""
    from .experiments import collect_job_results as collect
    return collect(job_id)


def compare_jobs(baseline_job_id: str, candidate_job_id: str, metric: str, direction: str = "minimize", minimum_improvement: float = 0.0) -> dict:
    """Compare valid trials in a shared approved scope and save the measured improvement."""
    from .experiments import compare_jobs as compare
    return compare(baseline_job_id, candidate_job_id, metric, direction, minimum_improvement)


def list_jobs(project_id: str = "paper-research", limit: int = 30, offset: int = 0, pending_only: bool = False) -> dict:
    """List this direction's registered jobs without SSH or creating new tasks."""
    from .recovery import list_jobs as listing
    return listing(project_id, limit, offset, pending_only)


def retry_job_dispatch(job_id: str) -> dict:
    """Recover an uncertain SSH submission using the SAME job ID and run slot."""
    from .recovery import retry_job_dispatch as retry
    return retry(job_id)


def resume_project(project_id: str = "paper-research", collect_finished: bool = True, limit: int = 30, offset: int | None = None) -> dict:
    """Reconnect to existing jobs and collect finished results; never start replacement experiments."""
    from .recovery import resume_project as resume
    return resume(project_id, collect_finished, limit, offset)


def export_project_report(project_id: str, metric: str, direction: str = "minimize") -> dict:
    """Build CSV, measured plot and Markdown report from saved project evidence."""
    from .reporting import export_project_report as export
    return export(project_id, metric, direction)


def _api_key():
    cfg = config()["review"]
    if os.environ.get(cfg["key_env"]):
        return os.environ[cfg["key_env"]]
    private_key = Path.home() / ".config/ai2research/review.key"
    if sys.platform != "win32" and private_key.is_file():
        if private_key.stat().st_mode & 0o077:
            raise ValueError("Review credential must have owner-only permissions")
        key = private_key.read_text().strip()
        if not key:
            raise ValueError("Review credential is empty")
        return key
    credential = local_dir() / "dashscope.dpapi"
    if sys.platform != "win32" or not credential.is_file():
        raise ValueError("Configure DASHSCOPE_API_KEY; no key is stored in project config")
    # PowerShell's default SecureString export is a hex-encoded DPAPI blob.
    # Decrypt directly to avoid PowerShell 5/7 encoding differences on Chinese paths.
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    raw = bytes.fromhex(credential.read_text(encoding="utf-8-sig").strip())
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    encrypted = Blob(len(raw), buffer)
    decrypted = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(encrypted), None, None, None, None, 0, ctypes.byref(decrypted)):
        raise ValueError("Cannot decrypt credential as current Windows user")
    try:
        key = ctypes.string_at(decrypted.data, decrypted.size).decode("utf-16le")
    finally:
        ctypes.windll.kernel32.LocalFree(decrypted.data)
    if not key:
        raise ValueError("Credential decryption returned empty data; no API request sent")
    return key


def review_topic(topic: str, project_id: str = "paper-research", max_output_tokens: int = 256) -> dict:
    """One bounded external review via the exact user-supplied Aliyun model; no retries/fallbacks.

    Stores request, response, actual model and token usage. Global call budget is atomic.
    """
    cfg = config()["review"]
    maximum_input = cfg.get("max_input_chars", 20000)
    if not 1 <= len(topic) <= maximum_input:
        raise ValueError(f"Topic must contain 1–{maximum_input} characters")
    if not 1 <= max_output_tokens <= cfg["max_output_tokens"]:
        raise ValueError("Output exceeds configured token allowance")
    if review_limit() <= 0:
        raise ValueError("No external review budget is configured for this installation")
    key = _api_key()
    request_id = reserve_review(project_id)
    record("review_input", {"request_id": request_id, "topic": topic, "provider": cfg["provider"], "requested_model": cfg["model"], "api_base": cfg["api_base"], "enable_thinking": cfg.get("enable_thinking")}, project_id)
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm
    litellm.suppress_debug_info = True
    try:
        response = litellm.completion(
            model="openai/" + cfg["model"], api_base=cfg["api_base"], api_key=key,
            messages=[{"role": "system", "content": "你是独立研究审查者。根据给定证据指出关键问题和最小验证建议；不编造文献、实验或已完成工作。"},
                      {"role": "user", "content": topic}],
            max_tokens=max_output_tokens, timeout=cfg["timeout_seconds"], num_retries=0,
            **({"extra_body": {"enable_thinking": cfg["enable_thinking"]}} if "enable_thinking" in cfg else {}),
        )
    except Exception as exc:
        # Provider exception strings may contain headers or credentials: never log them.
        return record("review_error", {"request_id": request_id, "error_type": type(exc).__name__,
                                      "http_status": getattr(exc, "status_code", None), "requested_model": cfg["model"],
                                      "next_action": "Verify endpoint/model entitlement; no automatic retry or model substitution"}, project_id)
    return record("review_result", {"request_id": request_id, "provider": cfg["provider"], "requested_model": cfg["model"],
                                    "actual_model": response.model, "usage": response.usage.model_dump() if response.usage else {},
                                    "response": response.choices[0].message.content,
                                    "finish_reason": response.choices[0].finish_reason, "cost": None,
                                    "enable_thinking": cfg.get("enable_thinking"),
                                    "cost_note": "No verified pricing configured; token/call bounds are not a currency cap"}, project_id)


async def arxiv_call(tool: str, arguments: dict) -> dict:
    """Call the isolated upstream arxiv MCP server; preserve errors as evidence."""
    python = _tool_python("arxiv")
    if not python.is_file():
        return {"status": "unavailable", "reason": f"arXiv environment is missing: {python}"}
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    paper_dir = local_dir() / "papers"
    transport = StdioTransport(command=str(python), args=["-m", "arxiv_mcp_server", "--storage-path", str(paper_dir)],
                               cwd=str(ROOT), env={"ARXIV_MAX_TOTAL_TIME": "35", "ARXIV_MAX_RETRIES": "0"})
    async with Client(transport, timeout=50) as client:
        if tool == "list_tools":
            return {"tools": [{"name": t.name, "schema": t.inputSchema} for t in await client.list_tools()]}
        result = await client.call_tool(tool, arguments)
        value = {"tool": tool, "arguments": arguments, "is_error": result.is_error,
                 "content": [c.model_dump() for c in result.content]}
        record("literature_call", value)
        return value


async def search_papers(query: str, max_results: int = 3) -> dict:
    """Search arXiv through the isolated upstream MCP server."""
    return await arxiv_call("search_papers", {"query": query, "max_results": max(1, min(max_results, 10))})


def _search_arxiv_atom(query: str, max_results: int) -> dict:
    """Read the official Atom API when the arXiv MCP wrapper fails."""
    import httpx
    from xml.etree import ElementTree

    exact_id = re.fullmatch(r"id:(\d{4}\.\d{4,5}(?:v\d+)?)", query.strip())
    params = {"id_list": exact_id[1]} if exact_id else {"search_query": query}
    response = httpx.get("https://export.arxiv.org/api/query",
                         params={**params, "max_results": max_results}, timeout=10)
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    papers = []
    for entry in root.findall("atom:entry", ns):
        url = entry.findtext("atom:id", "", ns)
        if not (url.startswith(("https://arxiv.org/abs/", "http://arxiv.org/abs/")) and "/api/" not in url):
            raise ValueError("Unexpected entry in arXiv API response")
        papers.append({"url": url, "title": " ".join(entry.findtext("atom:title", "", ns).split()),
                       "summary": " ".join(entry.findtext("atom:summary", "", ns).split())})
    return {"source": "arXiv Atom API", "papers": papers}


async def fetch_paper(paper_id: str) -> dict:
    """Download original paper content through arxiv-mcp-server to local storage."""
    return await arxiv_call("download_paper", {"paper_id": paper_id})


async def read_paper(paper_id: str, start: int = 0, max_chars: int = 8000) -> dict:
    """Page through cached original paper content, with bounded output."""
    return await arxiv_call("read_paper", {"paper_id": paper_id, "start": max(0, start), "max_chars": max(1, min(max_chars, 12000))})


async def export_bibtex(paper_id: str) -> dict:
    """Build BibTeX from retrieved metadata; upstream has no native BibTeX tool."""
    if not re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", paper_id):
        raise ValueError("Expected modern arXiv ID")
    result = await arxiv_call("search_papers", {"query": "id:" + paper_id, "max_results": 1})
    metadata = json.loads(next(c["text"] for c in result["content"] if c.get("type") == "text"))
    papers = metadata.get("papers", [])
    if not papers or papers[0]["id"].split("v")[0] != paper_id.split("v")[0]:
        raise ValueError("No exact arXiv metadata match")
    item = papers[0]
    def escape(value):
        return "".join("\\" + c if c in "&%$#_{}" else c for c in value)
    identifier = paper_id.split("v")[0]
    entry = "@misc{arxiv" + identifier.replace(".", "") + ",\n"
    fields = {"title": item["title"], "author": " and ".join(item["authors"]), "year": item["published"][:4],
              "eprint": identifier, "archivePrefix": "arXiv", "url": "https://arxiv.org/abs/" + paper_id}
    entry += ",\n".join("  " + key + " = {" + escape(value) + "}" for key, value in fields.items()) + "\n}\n"
    output = local_dir() / "papers" / (paper_id + ".bib")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(entry, encoding="utf-8")
    return record("citation_export", {"bibtex": entry, "path": str(output), "metadata_source": "arxiv-mcp-server/search_papers"})


def read_code(repository: str, path: str, ref: str = "HEAD", start: int = 0, max_chars: int = 12000) -> dict:
    """Read a public GitHub source file, returning its immutable blob SHA for traceability."""
    import httpx
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected owner/repository")
    from urllib.parse import quote
    url = "https://api.github.com/repos/" + repository + "/contents/" + quote(path, safe="/")
    headers = {"User-Agent": "paper-research-harness"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    response = httpx.get(url, params={"ref": ref}, headers=headers, timeout=20)
    if response.status_code in (403, 429):
        # Public API quotas need not block source inspection. Resolve a real commit,
        # then fetch immutable raw content and compute Git's blob hash ourselves.
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            commit = ref
        else:
            if ref.startswith("-") or any(c in ref for c in "\r\n"):
                raise ValueError("Invalid Git ref")
            lookup = subprocess.run(["git", "ls-remote", "https://github.com/" + repository + ".git", ref,
                                      "refs/heads/" + ref, "refs/tags/" + ref + "^{}"], capture_output=True, timeout=25)
            lines = lookup.stdout.decode().splitlines()
            if lookup.returncode or not lines:
                raise ValueError("GitHub API limited and cannot resolve immutable Git ref")
            commit = lines[0].split()[0]
        raw_url = "https://raw.githubusercontent.com/" + repository + "/" + commit + "/" + quote(path, safe="/")
        raw = httpx.get(raw_url, timeout=20)
        raw.raise_for_status()
        if len(raw.content) > 2 * 1024 * 1024:
            raise ValueError("Use git checkout for large source files")
        text = raw.content.decode("utf-8")
        start = max(0, start)
        end = start + max(1, min(max_chars, 20000))
        digest = hashlib.sha1(b"blob " + str(len(raw.content)).encode() + b"\0" + raw.content).hexdigest()
        return record("code_read", {"repository": repository, "path": path, "commit": commit, "blob_sha": digest, "url": raw_url,
                "content": text[start:end], "next_start": end if end < len(text) else None, "total_chars": len(text),
                "retrieval": "immutable raw file after public API rate limit"})
    response.raise_for_status()
    item = response.json()
    if not isinstance(item, dict) or item.get("type") != "file" or item.get("encoding") != "base64":
        raise ValueError("Expected a small text file; use git checkout for large files")
    text = base64.b64decode(item["content"]).decode("utf-8")
    start = max(0, start)
    end = start + max(1, min(max_chars, 20000))
    return record("code_read", {"repository": repository, "path": path, "ref": ref, "blob_sha": item["sha"], "url": item["html_url"],
            "content": text[start:end], "next_start": end if end < len(text) else None, "total_chars": len(text)})


def find_code(query: str, max_results: int = 3) -> dict:
    """Search public GitHub repositories; no assumption that a GitHub plugin is connected."""
    import httpx
    response = httpx.get("https://api.github.com/search/repositories", params={"q": query, "per_page": max(1, min(max_results, 10))},
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "paper-research-harness"}, timeout=20)
    response.raise_for_status()
    repos = [{"name": item["full_name"], "url": item["html_url"], "default_branch": item["default_branch"], "description": item.get("description"),
              "license": (item.get("license") or {}).get("spdx_id")} for item in response.json()["items"]]
    return record("code_search", {"query": query, "repositories": repos})


def compare_runs(experiment_name: str = "toolchain-smoke", limit: int = 10) -> dict:
    """Read local MLflow metrics and params. Synthetic smoke runs are marked as such."""
    from mlflow import MlflowClient
    uri = "sqlite:///" + (local_dir() / "mlflow.sqlite3").as_posix()
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return {"experiment": experiment_name, "runs": []}
    runs = client.search_runs([experiment.experiment_id], max_results=max(1, min(limit, 100)))
    return {"experiment": experiment_name, "runs": [{"run_id": r.info.run_id, "status": r.info.status,
              "params": r.data.params, "metrics": r.data.metrics, "tags": r.data.tags} for r in runs]}


def tuning_smoke() -> dict:
    """Three CPU synthetic Optuna trials; persist and compare in MLflow, never research evidence."""
    import mlflow
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    mlflow.set_tracking_uri("sqlite:///" + (local_dir() / "mlflow.sqlite3").as_posix())
    mlflow.set_experiment("toolchain-smoke")
    study = optuna.create_study(storage="sqlite:///" + (local_dir() / "optuna.sqlite3").as_posix(),
                                study_name="toolchain-smoke", load_if_exists=True,
                                sampler=optuna.samplers.RandomSampler(seed=42), direction="minimize")

    def objective(trial):
        x = trial.suggest_float("x", -2, 2)
        value = (x - 0.5) ** 2
        with mlflow.start_run(tags={"evidence_kind": "synthetic_toolchain_test", "optuna_trial": str(trial.number)}):
            mlflow.log_param("x", x)
            mlflow.log_metric("synthetic_loss", value)
        return value

    study.optimize(objective, n_trials=3)
    return record("tuning_smoke", {"study": study.study_name, "trials_total": len(study.trials), "best_value": study.best_value,
                                    "evidence_kind": "synthetic_toolchain_test"})


def compile_paper(source_path: str) -> dict:
    """Compile a local TeX file with shell escape disabled, preserving the build log."""
    source = Path(source_path).resolve(strict=True)
    if source.suffix != ".tex":
        raise ValueError("Expected .tex source")
    compiler = shutil.which("pdflatex") or shutil.which("tectonic") or (str(Path.home() / ".local/bin/tectonic") if (Path.home() / ".local/bin/tectonic").is_file() else None)
    if not compiler:
        return {"status": "unavailable", "reason": "pdflatex or tectonic not found"}
    build = local_dir() / "builds" / uuid.uuid4().hex
    build.mkdir(parents=True)
    log = build / "compile.log"
    args = [compiler, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "-output-directory=" + str(build)]
    if Path(compiler).name == "tectonic":
        args = [compiler, "--untrusted", "--keep-logs", "--outdir", str(build)]
    if "miktex" in compiler.lower():
        args.append("--disable-installer")
    try:
        result = subprocess.run(args + [str(source)], cwd=str(source.parent), capture_output=True, timeout=300)
        log.write_bytes(result.stdout + result.stderr)
    except subprocess.TimeoutExpired as exc:
        log.write_bytes((exc.stdout or b"") + (exc.stderr or b""))
        return {"status": "timeout", "pdf": None, "log": str(log), "build_dir": str(build)}
    pdf = build / (source.stem + ".pdf")
    success = result.returncode == 0 and pdf.exists() and pdf.read_bytes().startswith(b"%PDF-")
    return {"status": "completed" if success else "failed", "pdf": str(pdf) if success else None,
            "log": str(log), "build_dir": str(build), "exit_code": result.returncode}


def export_diagram(source_path: str, output_format: str = "svg") -> dict:
    """Export editable .drawio via a native desktop CLI when available."""
    source = Path(source_path).resolve(strict=True)
    if source.suffix != ".drawio" or output_format not in ("svg", "pdf"):
        raise ValueError("Expected .drawio source and svg/pdf output")
    candidates = [shutil.which("draw.io"), shutil.which("drawio"), os.environ.get("DRAWIO_EXE"), str(Path.home() / ".local/bin/drawio"),
                  "C:/Program Files/draw.io/draw.io.exe", "D:/drawio/draw.io/draw.io.exe", str(Path.home() / "AppData/Local/Programs/draw.io/draw.io.exe")]
    exe = next((p for p in candidates if p and Path(p).is_file()), None)
    if not exe:
        return {"status": "unavailable", "editable_source": str(source), "reason": "Set DRAWIO_EXE to installed draw.io CLI; no export claimed"}
    import tempfile
    target = source.with_suffix("." + output_format)
    # Export to a fresh location so a failed command cannot return an old artifact.
    with tempfile.TemporaryDirectory(prefix=".drawio-export-", dir=source.parent) as scratch:
        candidate = Path(scratch) / target.name
        try:
            result = subprocess.run([exe, "-x", "-f", output_format, "-e", "-o", str(candidate), str(source)],
                                    capture_output=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "source": str(source), "output": None}
        valid = False
        if result.returncode == 0 and candidate.is_file():
            data = candidate.read_bytes()
            valid = data.startswith(b"%PDF-") if output_format == "pdf" else b"<svg" in data[:2000]
        if valid:
            candidate.replace(target)
        return {"status": "completed" if valid else "failed", "source": str(source),
                "output": str(target) if valid else None, "exit_code": result.returncode}



def parse_pdf(source_path: str) -> dict:
    """Use an optional isolated Docling environment; never silently substitute a parser."""
    source = Path(source_path).resolve(strict=True)
    if source.suffix.lower() != ".pdf":
        raise ValueError("Expected PDF")
    exe = _tool_python("docling")
    if not exe.is_file():
        return {"status": "unavailable", "reason": "Docling isolated environment has not been installed"}
    output = local_dir() / "parsed" / hashlib.sha256(str(source).encode()).hexdigest()[:16]
    output.mkdir(parents=True, exist_ok=True)
    script = "from docling.document_converter import DocumentConverter; import sys,pathlib; r=DocumentConverter().convert(sys.argv[1]); pathlib.Path(sys.argv[2]).write_text(r.document.export_to_markdown(),encoding='utf-8')"
    target = output / "document.md"
    result = subprocess.run([str(exe), "-c", script, str(source), str(target)], capture_output=True, timeout=180)
    (output / "conversion.log").write_bytes(result.stdout + result.stderr)
    return {"status": "completed" if result.returncode == 0 and target.exists() else "failed", "markdown": str(target), "log": str(output / "conversion.log")}


# All public CLI/MCP calls share this boundary; nested service calls maintain once.
from .maintenance import instrument_service as _instrument_service
_instrument_service(globals())
