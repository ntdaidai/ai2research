"""Small, read-only tool inventory and registered paper/code links for the UI."""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import shutil
import tomllib
from urllib.parse import urlsplit

from .state import ROOT, config


def public_url(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return value
    except ValueError:
        return ""


def research_references(directory, project_id, manifest, artifacts):
    from .progress import Files
    files = Files(directory, project_id, artifacts)
    registered = files.artifacts.get("discovery/papers.json")
    payload = files.read_json("discovery/papers.json")
    if registered and not isinstance(payload, dict):
        return {"state": "needs_check", "items": [], "note": "参考清单与登记版本需要核对。"}
    papers = payload.get("papers", []) if isinstance(payload, dict) else []
    items = []
    for paper in papers:
        original = public_url(paper.get("paper_url"))
        pdf = original if urlsplit(original).path.lower().endswith(".pdf") else ""
        parsed = urlsplit(original)
        if parsed.hostname in {"arxiv.org", "www.arxiv.org"}:
            if parsed.path.startswith("/abs/"):
                pdf = "https://arxiv.org/pdf/" + parsed.path.removeprefix("/abs/")
            elif parsed.path.startswith("/pdf/"):
                pdf = original
        code = public_url(paper.get("code_url"))
        parsed_code = urlsplit(code)
        if parsed_code.hostname == "github.com" and parsed_code.path.endswith(".git"):
            code = parsed_code._replace(path=parsed_code.path.removesuffix(".git")).geturl()
        items.append({"title": paper.get("title") or paper.get("paper_id") or "参考论文",
            "paper_id": paper.get("paper_id", ""), "venue": paper.get("venue", ""),
            "date": paper.get("publication_date", ""), "paper_url": original, "pdf_url": pdf,
            "venue_url": public_url(paper.get("official_venue_url")), "code_url": code,
            "commit": paper.get("repository_commit", ""), "license": paper.get("license", "")})
    if not items and not registered:
        for seed in manifest.get("seed_papers", []):
            url = public_url(seed)
            if url:
                items.append({"title": seed, "paper_id": "", "venue": "种子论文", "date": "",
                    "paper_url": url, "pdf_url": url if urlsplit(url).path.lower().endswith(".pdf") else "",
                    "venue_url": "", "code_url": "", "commit": "", "license": ""})
    return {"state": "registered" if items else "empty", "items": items,
            "note": "选题阶段登记的候选论文与代码，随当前项目切换。" if items else "论文与代码确认后，会在这里列出。"}


def tool_inventory(*, root=ROOT, home=None, codex_home=None, settings=None):
    home = Path(home) if home is not None else Path.home()
    codex = Path(codex_home) if codex_home is not None else Path(os.environ.get("CODEX_HOME", home / ".codex"))
    root = Path(root)
    settings = config() if settings is None else settings
    items = []

    def add(identifier, kind, title, description, status, evidence):
        items.append({"id": identifier, "kind": kind, "title": title, "description": description,
                      "status": status, "evidence": evidence})

    skills = [
        ("ai2research", "研究项目操作", "研究任务、实验、审查与交付的项目入口。"),
        ("research-workflow", "研究工作流", "文献、选题、实验、写作与复核的方法指导。"),
        ("nature-polishing", "论文润色", "学术表达、翻译、精简及稿件排版。"),
        ("anti-defensive-writing", "清晰写作", "减少重复解释与不必要的防御性表达。"),
        ("drawio-skill", "可编辑绘图", "制作可编辑的研究方法与系统结构图。"),
    ]
    for name, title, description in skills:
        installed = (codex / "skills" / name / "SKILL.md").is_file()
        bundled = (root / "skills" / name / "SKILL.md").is_file()
        add(name, "skill", title, description, "已检测" if installed else "项目自带" if bundled else "未检测到",
            "用户技能文件存在" if installed else "项目技能文件存在" if bundled else "本机未发现相应技能文件")

    registered = set()
    try:
        with (codex / "config.toml").open("rb") as file:
            servers = tomllib.load(file).get("mcp_servers", {})
        registered = {name for name, value in servers.items() if not value.get("disabled", False) and value.get("enabled", True)}
    except (OSError, ValueError):
        pass
    core = (root / "tools/harness_mcp.py").is_file()
    has_registration = bool(registered & {"ai2research", "research_harness"})
    add("ai2research-mcp", "mcp", "ai2research MCP", "任务状态、论文检索、实验与成果工具入口。",
        "已登记" if has_registration else "项目适配" if core else "未检测到",
        "当前用户 MCP 配置中存在登记；未发起连接测试" if has_registration else "项目含 MCP 服务代码；连接由使用端配置" if core else "未发现服务入口")
    arxiv = root / ".venv-arxiv"
    upstream = (arxiv / "bin/python").is_file() and any(arxiv.glob("lib/python*/site-packages/arxiv_mcp_server"))
    add("arxiv-mcp", "mcp", "arXiv 检索适配", "通过独立运行时检索论文与读取原文。", "本机适配" if upstream else "未检测到",
        "独立 Python 和模块存在；未联网检索" if upstream else "尚未检测到完整独立运行时")

    plugin_root = codex / "plugins/cache/openai-curated-remote"
    for name, title, skill, description in [
        ("sites", "Sites", "sites", "网页设计、实现与本地展示流程。"),
        ("visualize", "Visualize", "visualize", "交互式图表和原理演示的设计规范。"),
        ("pages", "Pages", "write-page", "整理和维护研究内容页面。"),
    ]:
        found = any((plugin_root / name).glob(f"*/skills/{skill}/SKILL.md"))
        add(name, "plugin", title, description, "技能包已检测" if found else "未检测到",
            "检测本机插件技能包；账号连接由使用端管理" if found else "本机未检测到相应插件技能包")

    for executable, title, description in [
        ("tectonic", "LaTeX / Tectonic", "把论文源码编译成 PDF。"),
        ("drawio", "draw.io", "导出可编辑结构图与矢量图。"),
    ]:
        found = bool(shutil.which(executable)) or (home / ".local/bin" / executable).is_file()
        add(executable, "runtime", title, description, "已检测" if found else "未检测到",
            "本机可执行入口存在；未启动执行" if found else "未检测到可执行入口")
    try:
        version = importlib.metadata.version("matplotlib")
    except importlib.metadata.PackageNotFoundError:
        version = ""
    add("matplotlib", "runtime", "Matplotlib", "从实验结果生成论文图表。", "已安装" if version else "未检测到",
        "当前 Python 环境版本 " + version if version else "当前 Python 环境未发现安装记录")
    model = settings.get("review", {}).get("model", "")
    add("external-review", "model", model or "外部评审模型", "用于实验与稿件的外部审查。", "已配置" if model else "未配置",
        "仅显示模型配置；未发送请求或探测凭据" if model else "尚无模型配置")
    return {"items": items, "note": "分别展示本机技能、MCP登记、插件技能包与模型配置；不会触发联网或付费检查。"}
