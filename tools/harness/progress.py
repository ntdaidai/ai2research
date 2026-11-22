"""Research milestones projected from recorded work, without advancing the workflow."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


LABELS = {"completed": "已完成", "in_progress": "进行中", "revision": "待修订",
          "needs_check": "待核对", "awaiting": "待验收", "pending": "未开始"}
ACTIVE = {"queued", "starting", "waiting_resources", "running", "evaluating"}
TERMINAL = {"completed", "failed", "timeout", "cancelled", "canceled", "resource_unavailable"}


class Files:
    """Verify only milestone documents, never datasets or feature tensors."""
    def __init__(self, directory, project_id, artifacts):
        self.base = Path(directory) / "projects"
        self.root = self.base / project_id
        self.artifacts = {a["path"]: a for a in artifacts}
        self.cache = {}
        self.remaining = 32 * 1024 * 1024

    def content(self, item):
        if not item:
            return None
        key = (item["path"], item.get("sha256"))
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in item["path"]:
            return None
        cursor = self.base
        for part in (self.root.name, *path.parts):
            if cursor.is_symlink():
                return None
            cursor = cursor / part
        try:
            if cursor.is_symlink() or not cursor.resolve().is_relative_to(self.root.resolve()):
                return None
            size = cursor.stat().st_size
            if size > min(8 * 1024 * 1024, self.remaining):
                return None
            raw = cursor.read_bytes()
            self.remaining -= len(raw)
            if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
                return None
            self.cache[key] = raw
            return raw
        except OSError:
            return None

    def valid(self, item):
        return self.content(item) is not None

    def read_json(self, path):
        try:
            return json.loads(self.content(self.artifacts.get(path)) or "null")
        except (ValueError, UnicodeError):
            return None

    def verified_path(self, relative, digest, *, max_bytes):
        """Stream-check larger delivery files without using the document read budget."""
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative:
            return None
        cursor = self.base
        for part in (self.root.name, *path.parts):
            if cursor.is_symlink():
                return None
            cursor = cursor / part
        try:
            if cursor.is_symlink() or not cursor.resolve().is_relative_to(self.root.resolve()):
                return None
            if not cursor.is_file() or cursor.stat().st_size > max_bytes:
                return None
            calculated = hashlib.sha256()
            with cursor.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    calculated.update(chunk)
            return cursor if calculated.hexdigest() == digest else None
        except OSError:
            return None


def finalized_delivery(files, delivery, reviews):
    """Reuse a real finalized delivery's gate receipt, only for unchanged files."""
    if delivery.get("status") != "finalized" or delivery.get("missing"):
        return set()
    try:
        relative = Path(delivery["archive"]).relative_to(files.root).as_posix()
        archive_path = files.verified_path(relative, delivery["archive_sha256"], max_bytes=128 * 1024 * 1024)
        if archive_path is None:
            return set()
        with zipfile.ZipFile(archive_path) as archive:
            if any(archive.getinfo(name).file_size > 4 * 1024 * 1024 for name in ("DELIVERY_MANIFEST.json", "WORKFLOW_RECORD.json")):
                return set()
            manifest = json.loads(archive.read("DELIVERY_MANIFEST.json"))
            workflow = json.loads(archive.read("WORKFLOW_RECORD.json"))
        if manifest.get("status") != "finalized" or manifest.get("missing") or manifest.get("project_id") != files.root.name:
            return set()
        remaining = 128 * 1024 * 1024
        for item in manifest.get("files", []):
            if item["path"] == "WORKFLOW_RECORD.json":
                continue
            current = files.artifacts.get(item["path"])
            if not current or current["sha256"] != item["sha256"]:
                return set()
            path = files.verified_path(item["path"], item["sha256"], max_bytes=min(32 * 1024 * 1024, remaining))
            if path is None:
                return set()
            remaining -= path.stat().st_size
        if not manifest.get("files"):
            return set()
        if any(r["created_utc"] > manifest.get("created_utc", "") for r in reviews):
            return set()
        return {r["stage"] for r in workflow.get("reviews", []) if r.get("state") == "ready"}
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return set()


def project_progress(db, directory, manifest, project, artifacts, jobs, reviews):
    project_id = project["id"]
    files = Files(directory, project_id, artifacts)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tasks = [json.loads(r[0]) for r in db.execute("SELECT payload FROM workflow_tasks WHERE project_id=?", (project_id,))] if "workflow_tasks" in tables else []
    task_map = {t["task_id"]: t for t in tasks}
    artifacts_by_id = {}
    task_validity = {}

    def task_valid(task):
        identifier = task["task_id"]
        if identifier not in task_validity:
            ids = task.get("artifact_ids", [])
            for artifact_id in ids:
                if artifact_id not in artifacts_by_id:
                    row = db.execute("SELECT payload FROM workflow_artifacts WHERE project_id=? AND artifact_id=?", (project_id, artifact_id)).fetchone()
                    artifacts_by_id[artifact_id] = json.loads(row[0]) if row else None
            task_validity[identifier] = task.get("state") == "completed" and bool(ids) and all(files.valid(artifacts_by_id[i]) for i in ids)
        return task_validity[identifier]

    def events(kind):
        return [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE project=? AND kind=? ORDER BY rowid", (project_id, kind))]

    def of_kind(*kinds):
        return [a for a in artifacts if a["kind"] in kinds]

    def check(label, done, detail=""):
        return {"label": label, "done": bool(done), "detail": detail}

    stages = []

    def add(identifier, title, icon, checks, summary, next_action, *, state=None, evidence=(), notes=()):
        done = sum(c["done"] for c in checks)
        status = state or ("completed" if done == len(checks) else "in_progress" if done else "pending")
        stages.append({"id": identifier, "title": title, "icon": icon, "state": status,
                       "state_label": LABELS[status], "completed_checks": done, "total_checks": len(checks),
                       "checks": checks, "summary": summary, "next_action": next_action,
                       "evidence": list(dict.fromkeys(evidence))[:6], "notes": list(notes)})

    discovery = manifest.get("discovery", {})
    seeds = manifest.get("seed_papers", [])
    add("intake", "选题入口", "spark", [check("方向、要求或种子论文已记录", discovery or seeds)],
        discovery.get("domain") or f"已接收 {len(seeds)} 篇种子论文", "核查候选论文与官方代码。",
        evidence=["discovery/request.json"] if discovery else [])

    shortlist = files.read_json("discovery/papers.json") or {}
    papers = shortlist.get("papers", []) if isinstance(shortlist, dict) else []
    screening = [t for t in tasks if t.get("role") == "screening"]
    target = discovery.get("paper_count") or len(seeds) or len(screening)
    screened = sum(task_valid(t) for t in screening)
    add("papers", "文章筛选", "book", [check("候选论文数量达到本项目要求", target and max(len(papers), len(seeds)) >= target, f"{max(len(papers),len(seeds))}/{target} 篇"),
        check("逐篇阅读与筛选记录已交接", target and screened >= target, f"{screened}/{target} 篇")],
        f"候选 {max(len(papers),len(seeds))} 篇 · 已完成阅读交接 {screened} 篇", "补齐原文核查与逐篇筛选记录。",
        evidence=["discovery/papers.json", *[p for t in screening for p in t.get("output_paths", [])]])
    stages[-1]["items"] = [{"label": p.get("title", "论文"), "url": p.get("paper_url", ""),
                            "detail": " · ".join(filter(None, [p.get("venue"), p.get("publication_date")]))} for p in papers]

    pinned = [p for p in papers if p.get("code_url") and re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", p.get("repository_commit", ""))
              and p.get("license", "").lower() not in {"", "unknown", "none", "unlicensed", "pending"} and p.get("code_status") == "verified"]
    add("code", "代码核查", "code", [check("仓库、固定版本与许可已核查登记", target and len(pinned) >= target, f"{len(pinned)}/{target} 个仓库"),
        check("核心实现阅读已随筛选任务交接", target and screened >= target)],
        f"已登记 {len(pinned)} 个固定版本仓库 · 阅读交接 {screened} 份", "核查作者仓库、版本、许可和可运行入口。",
        evidence=["discovery/papers.json"])
    stages[-1]["items"] = [{"label": p.get("code_url", "").removeprefix("https://"), "url": p.get("code_url", ""),
                            "detail": p.get("repository_commit", "")[:12] + " · " + p.get("license", "")} for p in papers]

    attachments = events("job_evidence_attached")
    successful = {j["job_id"] for j in jobs if j["state"] == "completed" and j["collected"] and not j["metrics_error"]}
    phases = {phase: {e["job_id"] for e in attachments if e.get("phase") == phase and e.get("job_id") in successful}
              for phase in ("baseline", "search", "verification", "ablation")}
    selections = events("workflow_selection")
    selection = selections[-1] if selections else {}
    chosen = task_map.get(selection.get("task_id"))
    add("selection", "可行课题", "node", [check("已记录候选选择与依据", selection.get("reason")),
        check("所选候选筛选证据仍有效", chosen and task_valid(chosen)),
        check("已归档可运行基线", phases["baseline"], f"{len(phases['baseline'])} 次基线运行")],
        f"{'候选已选定' if selection else '尚未选定候选'} · {len(phases['baseline'])} 次基线结果已归档",
        "根据阅读与有界小试确认可推进的课题。", evidence=chosen.get("output_paths", []) if chosen else [])
    if chosen:
        stages[-1]["notes"] = [chosen.get("title", ""), "运行可行性已有记录；改进有效性继续由确认实验和审查判断。"]

    proposals = db.execute("SELECT COUNT(*) FROM events WHERE project=? AND kind='experiment_proposal'", (project_id,)).fetchone()[0]
    approvals = db.execute("SELECT COUNT(*) FROM events WHERE project=? AND kind='experiment_approval'", (project_id,)).fetchone()[0]
    deliveries = events("delivery_exported")
    delivery = deliveries[-1] if deliveries else {}
    finalized_stages = finalized_delivery(files, delivery, reviews)
    delivered = {"experiment", "manuscript"}.issubset(finalized_stages)
    preparation = [t for t in tasks if t.get("role") in {"method_designer", "implementation", "evaluation"}]
    preparation_done = sum(task_valid(t) for t in preparation)
    handoff_complete = bool(preparation) and preparation_done == len(preparation)
    accepted_preparation = bool(delivered and successful and proposals and approvals)
    preparation_gap = preparation_done < len(preparation)
    preparation_checks = [check("实验方案已冻结", proposals, f"{proposals} 份冻结方案"), check("执行范围已有批准记录", approvals),
        check("准备交接或正式交付验收有效", handoff_complete or accepted_preparation,
              "正式双审与交付验收已核实" if accepted_preparation else f"{preparation_done}/{len(preparation)} 项交接")]
    add("preparation", "实验准备", "code", preparation_checks,
        f"冻结方案 {proposals} 份 · " + ("准备结果已随正式交付验收" if accepted_preparation else f"准备交接 {preparation_done}/{len(preparation)} 项"),
        "实验准备已验收，可查看交付成果。" if accepted_preparation else "按已有代码和方案补齐阶段交接；已有实验无需重跑。",
        state="needs_check" if successful and preparation_gap and not accepted_preparation else None,
        notes=[f"历史准备任务交接 {preparation_done}/{len(preparation)} 项，任务板保留原状态。当前准备结果以文件一致性通过核验的正式双审与交付为完成依据。"]
              if preparation_gap and accepted_preparation else ["实际已有实验结果，部分准备任务仍待交接；页面未代填完成。"]
              if successful and preparation_gap else [])

    counts = Counter(j["state"] for j in jobs)
    running = sum(counts[s] for s in ACTIVE)
    unsettled = sum(j["state"] not in TERMINAL for j in jobs)
    abnormal = sum(v for k,v in counts.items() if k in TERMINAL and k != "completed")
    add("experiments", "实验运行", "flask", [check("已提交真实作业", jobs), check("已提交批次运行已结束", jobs and not unsettled),
        check("成功结果已回收", successful and len(successful) == counts["completed"])],
        f"{len(jobs)} 次提交 · {counts['completed']} 完成 · {running} 运行/排队 · {abnormal} 次异常",
        "查看当前运行与失败原因，回收已完成结果。" if unsettled else "排查失败或评价记录，回收有效结果。" if not successful else "已提交批次已结束，继续复核与审查。",
        state="needs_check" if jobs and not unsettled and (not successful or len(successful) != counts["completed"]) else None,
        notes=["这里统计已提交的批次，不把试验预算上限当成必做次数。"])

    independent = [t for t in tasks if t.get("role") == "verification" and task_valid(t)]
    add("verification", "复核消融", "check", [check("确认性结果已归档", phases["verification"], f"{len(phases['verification'])} 次"),
        check("消融结果已归档", phases["ablation"], f"{len(phases['ablation'])} 次"), check("独立复核任务已完成交接", independent)],
        f"{len(phases['verification'])} 次确认 · {len(phases['ablation'])} 次消融 · {len(independent)} 项独立复核交接",
        "完成独立确认与关键消融，保留负结果和复核意见。")

    def review_stage(identifier, title, stage):
        record = next((r for r in reviews if r["stage"] == stage), None)
        roles = {r["reviewer"] for r in record["reviews"]} if record else set()
        major = sum(r["major_issues"] + r["blockers"] for r in record["reviews"]) if record else 0
        status = "revision" if record and record["recorded_status"] == "revision_requested" else "awaiting" if record else "pending"
        checks = [check("本轮材料已提交审查", record), check("独立审查意见已返回", "agent" in roles),
                  check("外部审查意见已返回", "deepseek" in roles), check("本轮无重大问题或阻断项", record and {"agent", "deepseek"}.issubset(roles) and not major and record["recorded_status"] != "revision_requested"),
                  check("当前版本正式验收记录有效", stage in finalized_stages)]
        if all(c["done"] for c in checks):
            status = "completed"
        missing = [name for role,name in (("agent","独立审查"),("deepseek","外部审查")) if role not in roles]
        summary = f"本轮 {len(roles)}/2 份意见 · {major} 项重大/阻断问题" if record else "尚未提交本阶段材料"
        if status == "completed":
            summary += " · 当前版本已验收"
        elif record and not missing and not major and record["recorded_status"] != "revision_requested":
            summary += " · 当前版本验收待核对"
        add(identifier, title, "check", checks, summary,
            "处理本轮问题，更新证据后按原门槛重新审查。" if status == "revision" else "收齐意见并核对当前版本的审查覆盖与正式验收。",
            state=status, notes=["显示最近一轮意见；分数与意见返回不代替当前版本的正式质量验收。"])

    review_stage("experiment_review", "实验审查", "experiment")

    chapters = [t for t in tasks if t.get("role") == "chapter_writer"]
    chapter_done = sum(task_valid(t) for t in chapters)
    build = files.read_json("paper/build.json") or {}
    pdf = files.artifacts.get("paper/paper.pdf")
    inputs = build.get("inputs", {})
    current_build = bool(inputs) and bool(pdf) and files.valid(pdf) and build.get("pdf_sha256") == pdf["sha256"] and all(files.valid({"path":p,"sha256":sha}) for p,sha in inputs.items())
    add("manuscript", "论文初稿", "file", [check("各章节已完成并有效交接", chapters and chapter_done == len(chapters), f"{chapter_done}/{len(chapters)} 章"),
        check("当前源码与图表已编译为PDF", current_build)], f"章节交接 {chapter_done}/{len(chapters)} · {'当前PDF已构建' if current_build else '当前PDF待构建或核对'}",
        "补齐章节、统一数字与引用，再构建当前版本PDF。", state="needs_check" if pdf and not current_build else None,
        evidence=["paper/build.json","paper/paper.pdf"])

    diagrams = of_kind("diagram_source")
    rendered = of_kind("diagram_pdf", "diagram_svg", "figure_pdf", "figure_png")
    valid_rendered = [a for a in rendered if files.valid(a)]
    figure_sources = of_kind("figure_source", "figure_provenance")
    add("figures", "图表制作", "layers", [check("可编辑图源或绘图脚本已登记", any(files.valid(a) for a in [*diagrams,*figure_sources])),
        check("图表已导出且与登记一致", valid_rendered, f"{len(valid_rendered)} 个输出文件")],
        f"{len(diagrams)} 份可编辑结构图源 · {len(valid_rendered)} 个有效图表输出", "从锁定结果生成图表，保留可编辑源与生成记录。",
        evidence=[a["path"] for a in valid_rendered])

    review_stage("manuscript_review", "稿件审查", "manuscript")

    missing = delivery.get("missing", [])
    add("delivery", "成果交付", "layers", [check("交付包已生成", delivery), check("交付记录无缺项", delivery and not missing),
        check("归档、当前文件与双审验收记录一致", delivered)],
        ("正式交付已验收" if delivered else "已有交付包" if delivery else "尚未生成交付包") + (f" · {len(missing)} 项未满足" if missing else ""),
        "核对当前代码、结果、图表、稿件和双审覆盖后，生成正式交付包。",
        state="completed" if delivered else "needs_check" if delivery else "pending", notes=["已有归档不等于当前版本已完成交付验收。"] if delivery and not delivered else [])

    completed = sum(s["state"] == "completed" for s in stages)
    attention = [s for s in stages if s["state"] in {"revision", "needs_check"}]
    active = [s for s in stages if s["state"] in {"in_progress", "awaiting"}]
    revisions = [s for s in attention if s["state"] == "revision"]
    focus = (revisions or active or attention or [s for s in stages if s["state"] != "completed"] or stages)[0]
    if completed == len(stages):
        for stage in stages:
            stage["next_action"] = "本环节已完成，可在成果页查看正式论文、图表与实验记录。"
        focus = stages[-1]
        focus["next_action"] = "全部环节已有完成依据，可查看当前成果与复现材料。"
    if completed < len(stages):
        for stage in stages:
            if stage["state"] == "completed":
                stage["next_action"] = "本环节已完成。当前优先处理" + focus["title"] + "：" + focus["next_action"]
    return {"stages": stages, "completed_stages": completed, "total_stages": len(stages),
            "attention_stages": len(attention), "focus_stage": focus["id"],
            "paused": project["execution_state"] == "paused",
            "next_action": "项目已暂停，保留当前进展；等待明确恢复。" if project["execution_state"] == "paused" else focus["next_action"],
            "meaning": "已完成环节占比，不是剩余时间预测；质量验收沿用原研究门槛。"}
