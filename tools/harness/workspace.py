"""Persistent task hand-offs for a conversation-led research project.

The control plane records work; it neither spawns model sessions nor approves or
starts experiments. All artifact paths are relative to the project workspace.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path, PurePosixPath

from .state import ROOT, connect, local_dir, now, execution_state, require_active_project

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def _identifier(value: str, what: str = "project_id") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in (".", ".."):
        raise ValueError(f"Invalid {what}")
    return value


def project_root(project_id: str) -> Path:
    """Return a confined workspace path without creating a project."""
    _identifier(project_id)
    base = local_dir() / "projects"
    target = base / project_id
    if base.is_symlink() or target.is_symlink():
        raise ValueError("Project paths must not be symbolic links")
    if target.resolve().parent != base.resolve():
        raise ValueError("Project path escapes workspace")
    return target


def _relative_path(project_id: str, path: str, *, must_exist: bool = False) -> Path:
    if not isinstance(path, str) or not path.strip() or "\\" in path:
        raise ValueError("Use a nonempty project-relative POSIX path")
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() in (".", ""):
        raise ValueError("Artifact path must be project-relative")
    base = project_root(project_id)
    target = base.joinpath(*relative.parts)
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not allowed in project artifact paths")
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("Artifact path escapes project")
    if must_exist and not target.is_file():
        raise ValueError("Artifact must be an existing regular file")
    return target


def _db():
    db = connect()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS workflow_projects (
            project_id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workflow_tasks (
            task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            task_key TEXT, payload TEXT NOT NULL,
            UNIQUE(project_id, task_key));
        CREATE INDEX IF NOT EXISTS workflow_tasks_project ON workflow_tasks(project_id);
        CREATE TABLE IF NOT EXISTS workflow_artifacts (
            artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS workflow_artifacts_project ON workflow_artifacts(project_id);
    """)
    return db


def _project(db, project_id):
    project_root(project_id)
    row = db.execute("SELECT manifest FROM workflow_projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise ValueError("Project does not exist; call start_project first")
    return json.loads(row["manifest"])


def _event(db, project_id, kind, payload):
    payload = {key: value for key, value in payload.items() if key != "lease_token"}
    db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
               (uuid.uuid4().hex, project_id, kind, now(), json.dumps(payload, ensure_ascii=False)))


def _save_task(db, task):
    db.execute("UPDATE workflow_tasks SET payload=? WHERE task_id=? AND project_id=?",
               (json.dumps(task, ensure_ascii=False), task["task_id"], task["project_id"]))


def _task(db, project_id, task_id):
    row = db.execute("SELECT payload FROM workflow_tasks WHERE task_id=? AND project_id=?", (task_id, project_id)).fetchone()
    if row is None:
        raise ValueError("Task does not belong to project")
    return json.loads(row["payload"])


def _write_once(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Workspace entry is not a regular file")


def _materialize_project(manifest):
    project_id = manifest["project_id"]
    base = project_root(project_id)
    base.mkdir(parents=True, exist_ok=True)
    for folder in ("papers", "candidates", "experiments", "verification", "ablations", "reviews", "paper", "figures", "delivery", "tasks"):
        target = _relative_path(project_id, folder)
        target.mkdir(exist_ok=True)
    _write_once(_relative_path(project_id, "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    entry = f"""# 项目 {project_id} 的多对话入口

主控由活跃对话担任；本目录不运行后台自动科研服务。
先读 `{ROOT / 'AGENTS.md'}` 和 `{ROOT / 'RESEARCH_PLAN.md'}`，再用
`project_status(project_id=\"{project_id}\")` 获取任务、依赖和产物。
仅执行分配给自己的任务，先 `claim_task`，保存返回的 lease_token；
产物写入本项目，调用 `register_artifact` 后用 `update_task` 交接。
租约到期须重新领取；其他对话的产物以已登记摘要为准，不覆盖别人的文件。
长任务可用当前令牌调用 `update_task(state="running")` 续租。
正式训练使用现有 prepare_experiment / research_cycle，沿用真实实验批准和资源范围。
研究失败和负结果同样保存；工具测试不得标记为研究贡献。

按任务调用已安装技能：选题使用 brainstorming-research-ideas / creative-thinking-for-research；
系统论文使用 systems-paper-writing，ML 论文使用 ml-paper-writing；润色使用 nature-polishing / anti-defensive-writing；
可编辑架构图使用 drawio-skill，实验曲线使用 Matplotlib。
逐章写作，每章单独任务，统一大纲、术语和冻结证据后再整合。
独立评审从相同证据版本出发，分别记录子代理与外部模型真实身份及反馈。

CLI 与 MCP 使用同名工具，例如：

```bash
{ROOT / 'ai2research'} project_status '{{"project_id":"{project_id}"}}'
```

每个任务的 TASK.md 包含具体领取示例。复杂参数写入 JSON 文件，再用
`{ROOT / 'ai2research'} update_task @更新参数.json`，避免拼接 shell 命令。
"""
    _write_once(_relative_path(project_id, "AGENTS.md"), entry)


def start_project(project_id: str, seed_papers: list[str], title: str = "", evidence_kind: str = "research") -> dict:
    """Initialize one workspace and one screening task per supplied paper."""
    project_root(project_id)
    if (not isinstance(seed_papers, list) or not seed_papers or len(seed_papers) > 100
            or any(not isinstance(paper, str) or not paper.strip() or len(paper) > 4000 for paper in seed_papers)):
        raise ValueError("Supply 1–100 nonempty paper references")
    if not isinstance(title, str) or len(title) > 1000:
        raise ValueError("Invalid project title")
    if evidence_kind not in ("research", "toolchain_test"):
        raise ValueError("Project evidence_kind must be research or toolchain_test")
    papers = list(dict.fromkeys(paper.strip() for paper in seed_papers))
    title = title.strip() or project_id
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT manifest FROM workflow_projects WHERE project_id=?", (project_id,)).fetchone()
        if row:
            manifest = json.loads(row["manifest"])
            if (manifest["seed_papers"] != papers or manifest["title"] != title
                    or manifest.get("evidence_kind", "research") != evidence_kind):
                raise ValueError("Existing project has different seed papers or title")
        else:
            manifest = {"schema_version": 1, "project_id": project_id, "title": title,
                        "seed_papers": papers, "stage": "screening", "created_utc": now(),
                        "evidence_kind": evidence_kind,
                        "mode": "conversation-led", "research_execution": "requires_approved_scope"}
            db.execute("INSERT INTO workflow_projects VALUES (?,?)", (project_id, json.dumps(manifest, ensure_ascii=False)))
            _event(db, project_id, "workflow_project_started", manifest)
        _materialize_project(manifest)
    for index, paper in enumerate(papers, 1):
        create_task(project_id, f"核查候选论文 {index}", "screening", instructions=(
            f"种子论文：{paper}\n读取原文、核实官方代码和固定版本，记录可运行基线、数据、评测协议、"
            "资源成本、明确局限与可证伪改进。输出有来源的候选卡片；不要启动未批准研究实验。"
        ), output_paths=[f"candidates/paper-{index:02d}.md"], task_key=f"screen-paper-{index:02d}")
    return project_status(project_id)


def create_task(project_id: str, title: str, role: str, dependencies: list[str] | None = None,
                instructions: str = "", output_paths: list[str] | None = None, task_key: str = "") -> dict:
    """Create a resumable DAG task; an optional key makes retries idempotent."""
    if not isinstance(title, str) or not title.strip() or len(title) > 1000:
        raise ValueError("Task title is required")
    _identifier(role, "role")
    if task_key:
        _identifier(task_key, "task_key")
    if not isinstance(instructions, str) or len(instructions) > 50000:
        raise ValueError("Invalid task instructions")
    if dependencies is not None and (not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies)):
        raise ValueError("dependencies must be a list of task IDs")
    if output_paths is not None and (not isinstance(output_paths, list) or any(not isinstance(x, str) for x in output_paths)):
        raise ValueError("output_paths must be a list of relative paths")
    dependencies = list(dict.fromkeys(dependencies or []))
    paths = list(dict.fromkeys(_relative_path(project_id, path).relative_to(project_root(project_id)).as_posix() for path in output_paths or []))
    requested = {"title": title.strip(), "role": role, "dependencies": dependencies,
                 "instructions": instructions, "output_paths": paths}
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _project(db, project_id)
        for dependency in dependencies:
            _task(db, project_id, dependency)
        if task_key:
            existing = db.execute("SELECT payload FROM workflow_tasks WHERE project_id=? AND task_key=?", (project_id, task_key)).fetchone()
            if existing:
                task = json.loads(existing["payload"])
                if any(task[key] != value for key, value in requested.items()):
                    raise ValueError("Task key already exists with a different specification")
                _materialize_task(task)
                return {key: value for key, value in task.items() if key != "lease_token"}
        task_id = uuid.uuid4().hex
        task = {"task_id": task_id, "project_id": project_id, "task_key": task_key,
                **requested, "state": "pending", "owner": None, "lease_token": None,
                "lease_expires_unix": None, "attempts": 0, "created_utc": now(),
                "updated_utc": now(), "summary": "", "artifact_ids": [],
                "workspace": f"tasks/{task_id}", "prompt_path": f"tasks/{task_id}/TASK.md"}
        db.execute("INSERT INTO workflow_tasks VALUES (?,?,?,?)", (task_id, project_id, task_key or None, json.dumps(task, ensure_ascii=False)))
        _event(db, project_id, "workflow_task_created", task)
        _materialize_task(task)
    return task


def _materialize_task(task):
    path = _relative_path(task["project_id"], task["prompt_path"])
    text = (f"# {task['title']}\n\nproject_id: {task['project_id']}\ntask_id: {task['task_id']}\n"
            f"role: {task['role']}\n\n先读取项目 AGENTS.md；领取任务后保留租约令牌。\n\n"
            "先向主控报告已接收、实际是否启动及日志位置；不能启动时立即说明具体阻碍。"
            "按需读取摘要和相关函数，避免一次输出整份大日志或长报告。长计算启动后报告进程与进度，"
            "不要把准备工作说成正在运行；等待计算时继续可独立完成的工作。\n\n"
            f"{task['instructions']}\n\n输出路径：{json.dumps(task['output_paths'], ensure_ascii=False)}\n"
            f"依赖：{json.dumps(task['dependencies'])}\n\n"
            "结束时登记产物并更新任务；失败时保留原因，交由主控决定是否重领。\n\n"
            "领取示例（owner 改成当前代理名）：\n\n```bash\n"
            f"{ROOT / 'ai2research'} claim_task '"
            + json.dumps({"project_id": task['project_id'], "task_id": task['task_id'], "owner": "worker-1"}, ensure_ascii=False)
            + "'\n```\n\n"
            "领取后保留 lease_token。完成时以 register_artifact 登记每个输出，取得 artifact_id；"
            "然后调用 update_task，传 project_id、task_id、lease_token、state=completed、summary、artifact_ids。"
            "任务较长时用 state=running 续租，失败时用 state=failed 交接原因。\n")
    _write_once(path, text)


def claim_task(project_id: str, task_id: str, owner: str, lease_seconds: int = 900) -> dict:
    """Atomically acquire a ready task; expired leases can be reclaimed."""
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise ValueError("A nonempty agent owner is required")
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
        raise ValueError("lease_seconds must be 1–86400")
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _project(db, project_id)
        require_active_project(project_id, db)
        task = _task(db, project_id, task_id)
        if task["state"] == "completed":
            raise ValueError("Completed tasks cannot be claimed")
        if task["state"] == "running" and task["lease_expires_unix"] > time.time():
            raise ValueError("Task already has an active lease")
        for dependency in task["dependencies"]:
            predecessor = _task(db, project_id, dependency)
            if predecessor["state"] != "completed":
                raise ValueError("Task dependencies are not completed")
            _validate_artifacts(db, project_id, predecessor["artifact_ids"], predecessor["output_paths"])
        task.update(state="running", owner=owner.strip(), lease_token=uuid.uuid4().hex,
                    lease_expires_unix=time.time() + lease_seconds, lease_duration_seconds=lease_seconds,
                    attempts=task["attempts"] + 1,
                    updated_utc=now())
        _save_task(db, task)
        _event(db, project_id, "workflow_task_claimed", task)
    return task


def _validate_artifacts(db, project_id, artifact_ids, output_paths):
    if not artifact_ids:
        raise ValueError("Completed work must have at least one registered artifact")
    paths = set()
    for artifact_id in artifact_ids:
        row = db.execute("SELECT payload FROM workflow_artifacts WHERE artifact_id=? AND project_id=?", (artifact_id, project_id)).fetchone()
        if row is None:
            raise ValueError("Artifact does not belong to project")
        artifact = json.loads(row["payload"])
        path = _relative_path(project_id, artifact["path"], must_exist=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError("Registered artifact changed after registration")
        paths.add(artifact["path"])
    if not set(output_paths).issubset(paths):
        raise ValueError("Registered artifacts do not cover declared task outputs")


def update_task(project_id: str, task_id: str, lease_token: str, state: str, summary: str = "",
                artifact_ids: list[str] | None = None) -> dict:
    """Update work using the current unexpired lease; completion verifies outputs."""
    if state not in ("completed", "failed", "pending", "running"):
        raise ValueError("Use completed, failed, pending or running")
    if not isinstance(summary, str) or len(summary) > 20000:
        raise ValueError("Invalid task summary")
    if artifact_ids is not None and (not isinstance(artifact_ids, list) or any(not isinstance(x, str) for x in artifact_ids)):
        raise ValueError("artifact_ids must be a list of registered artifact IDs")
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _project(db, project_id)
        task = _task(db, project_id, task_id)
        if (task["state"] != "running" or not lease_token or task["lease_token"] != lease_token
                or task["lease_expires_unix"] <= time.time()):
            raise ValueError("Task update requires the current unexpired lease")
        ids = list(dict.fromkeys(artifact_ids if artifact_ids is not None else task["artifact_ids"]))
        if state == "completed":
            _validate_artifacts(db, project_id, ids, task["output_paths"])
        elif ids:
            _validate_artifacts(db, project_id, ids, [])
        task.update(state=state, summary=summary, artifact_ids=ids, updated_utc=now())
        if state != "running":
            task.update(lease_token=None, lease_expires_unix=None)
        else:
            task["lease_expires_unix"] = time.time() + task.get("lease_duration_seconds", 900)
        _save_task(db, task)
        _event(db, project_id, "workflow_task_updated", task)
    return task


def repair_changed_task_evidence(project_id: str, task_id: str, artifact_ids: list[str], summary: str) -> dict:
    """Rebind a completed task to current output revisions, preserving prior evidence and reviews."""
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 20000:
        raise ValueError("A nonempty repair summary is required")
    if (not isinstance(artifact_ids, list) or not artifact_ids
            or any(not isinstance(identifier, str) or not identifier for identifier in artifact_ids)
            or len(set(artifact_ids)) != len(artifact_ids)):
        raise ValueError("Select distinct current artifact IDs for repaired outputs")
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        _project(db, project_id)
        require_active_project(project_id, db)
        task = _task(db, project_id, task_id)
        if task["state"] != "completed":
            raise ValueError("Only an already completed task can rebind revised evidence")
        previous_ids = task["artifact_ids"]
        previous_paths = set()
        for identifier in previous_ids:
            row = db.execute("SELECT payload FROM workflow_artifacts WHERE project_id=? AND artifact_id=?", (project_id, identifier)).fetchone()
            if row is None:
                raise ValueError("Original completed-task evidence is missing from the registry")
            previous_paths.add(json.loads(row[0])["path"])
        expected_paths = set(task["output_paths"]) or previous_paths
        selected_paths = set()
        for identifier in artifact_ids:
            row = db.execute("SELECT payload FROM workflow_artifacts WHERE project_id=? AND artifact_id=?", (project_id, identifier)).fetchone()
            if row is None:
                raise ValueError("Repair artifact does not belong to this project")
            artifact = json.loads(row[0])
            current = db.execute("""SELECT artifact_id FROM workflow_artifacts WHERE project_id=?
                AND json_extract(payload,'$.path')=? ORDER BY rowid DESC LIMIT 1""", (project_id, artifact["path"])).fetchone()
            if current[0] != identifier:
                raise ValueError("Repair requires the latest registered output version")
            selected_paths.add(artifact["path"])
        if selected_paths != expected_paths or len(selected_paths) != len(artifact_ids):
            raise ValueError("Repair artifact paths must exactly match the task's declared outputs or original output paths")
        _validate_artifacts(db, project_id, artifact_ids, list(expected_paths))
        previous_summary = task["summary"]
        task.update(artifact_ids=list(artifact_ids), summary=summary.strip(), updated_utc=now())
        _save_task(db, task)
        _event(db, project_id, "workflow_task_evidence_repaired", {
            "task_id": task_id, "state": "completed", "old_artifact_ids": previous_ids,
            "new_artifact_ids": list(artifact_ids), "output_paths": sorted(expected_paths),
            "previous_summary": previous_summary, "summary": summary.strip(),
            "review_policy": "Existing reviews are preserved; changed source versions require their own review and manuscript rebuild."})
    return task


def register_artifact(project_id: str, path: str, kind: str, evidence_kind: str = "research", source_job_id: str = "") -> dict:
    """Record a file snapshot; does not assert scientific validity of its contents."""
    for value, label in ((kind, "kind"), (evidence_kind, "evidence_kind")):
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError(f"Invalid artifact {label}")
    if not isinstance(source_job_id, str) or len(source_job_id) > 200:
        raise ValueError("Invalid source job ID")
    file_path = _relative_path(project_id, path, must_exist=True)
    raw = file_path.read_bytes()
    if not raw and not (kind == "source_dependency" and file_path.name == "__init__.py"):
        raise ValueError("Empty files cannot be registered as work artifacts")
    path = file_path.relative_to(project_root(project_id)).as_posix()
    sha256 = hashlib.sha256(raw).hexdigest()
    identity = json.dumps([project_id, path, sha256, kind, evidence_kind, source_job_id])
    artifact_id = uuid.uuid5(uuid.NAMESPACE_URL, "ai2research:artifact:" + identity).hex
    with _db() as db:
        db.execute("BEGIN IMMEDIATE")
        manifest = _project(db, project_id)
        if manifest.get("evidence_kind") == "toolchain_test":
            evidence_kind = "toolchain_test"
            identity = json.dumps([project_id, path, sha256, kind, evidence_kind, source_job_id])
            artifact_id = uuid.uuid5(uuid.NAMESPACE_URL, "ai2research:artifact:" + identity).hex
        existing = db.execute("SELECT payload FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if existing:
            artifact = json.loads(existing["payload"])
            # Reverting a file to a previously registered revision makes that
            # snapshot current again. Keep its stable identity, but move its
            # registration after newer revisions for latest-by-path consumers.
            db.execute("DELETE FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,))
            db.execute("INSERT INTO workflow_artifacts VALUES (?,?,?)", (artifact_id, project_id, existing["payload"]))
            _event(db, project_id, "workflow_artifact", artifact)
            return artifact
        artifact = {"artifact_id": artifact_id, "project_id": project_id, "path": path,
                    "sha256": sha256, "bytes": len(raw), "kind": kind,
                    "evidence_kind": evidence_kind, "source_job_id": source_job_id, "created_utc": now()}
        db.execute("INSERT INTO workflow_artifacts VALUES (?,?,?)", (artifact_id, project_id, json.dumps(artifact, ensure_ascii=False)))
        _event(db, project_id, "workflow_artifact", artifact)
    return artifact


def list_artifacts(project_id: str) -> list[dict]:
    """Return all recorded snapshots, including earlier versions of changed files."""
    with _db() as db:
        _project(db, project_id)
        return [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM workflow_artifacts WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()]


def project_status(project_id: str) -> dict:
    """Read the shared board with dependency and lease readiness information."""
    with _db() as db:
        manifest = _project(db, project_id)
        control = execution_state(db, project_id)
        tasks = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM workflow_tasks WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()]
        artifacts = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM workflow_artifacts WHERE project_id=? ORDER BY rowid", (project_id,)).fetchall()]
        for task in tasks:
            task["artifacts_valid"] = None
            if task["state"] == "completed":
                try:
                    _validate_artifacts(db, project_id, task["artifact_ids"], task["output_paths"])
                    task["artifacts_valid"] = True
                except ValueError as exc:
                    task["artifacts_valid"] = False
                    task["integrity_error"] = str(exc)
    indexed = {task["task_id"]: task for task in tasks}
    for task in tasks:
        task["lease_expired"] = task["state"] == "running" and task["lease_expires_unix"] <= time.time()
        task["ready"] = control['execution_state'] != 'paused' and (task["state"] in ("pending", "failed") or task["lease_expired"]) and all(
            indexed[dependency]["state"] == "completed" and indexed[dependency]["artifacts_valid"]
            for dependency in task["dependencies"])
        # A public board need not reveal capability tokens to unrelated workers.
        task.pop("lease_token", None)
    return {**manifest, **control, "manifest": manifest, "root": str(project_root(project_id)), "tasks": tasks,
            "artifacts": artifacts, "ready_task_ids": [task["task_id"] for task in tasks if task["ready"]]}


def set_project_execution_state(project_id: str, state: str, user_instruction: str, reason: str = "") -> dict:
    """Persist explicit pause/resume; never cancel already running jobs implicitly."""
    if state not in ('paused', 'active') or not isinstance(user_instruction, str) or not user_instruction.strip():
        raise ValueError('Use paused/active and quote the actual user instruction')
    if len(user_instruction) > 20000 or len(reason) > 20000:
        raise ValueError('Execution-control text is too long')
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        _project(db, project_id)
        current = execution_state(db, project_id)
        if current.get('execution_state') == state and current.get('user_instruction') == user_instruction:
            return {**current, 'project_id': project_id, 'already_recorded': True}
        result = {'execution_state': state, 'user_instruction': user_instruction,
                  'reason': reason, 'updated_utc': now(),
                  'existing_jobs': 'Existing jobs are preserved; inspect and cancel individually if requested.'}
        _event(db, project_id, 'project_execution_control', result)
    return {**result, 'project_id': project_id}
