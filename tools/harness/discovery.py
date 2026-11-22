"""Domain-first research intake and evidence-backed candidate registration.

Discovery is performed by the active controller with real browsing tools. This
module persists the search contract and rejects incomplete candidate declarations;
it never pretends metadata validation downloads or reads a paper.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import re
from urllib.parse import urlparse

from . import workspace as ws
from .state import now

OFFICIAL_HOSTS = {"openreview.net", "proceedings.mlr.press", "neurips.cc", "papers.nips.cc", "iclr.cc", "icml.cc",
                  "thecvf.com", "cv-foundation.org", "aaai.org", "ijcai.org", "acm.org", "aclweb.org",
                  "aclanthology.org", "ieee.org", "doi.org", "springer.com", "springernature.com", "jmlr.org", "jmlr.csail.mit.edu"}

TOP_VENUES = {"ICLR", "ICML", "NEURIPS", "CVPR", "ICCV", "ECCV", "AAAI", "IJCAI",
              "KDD", "ACL", "EMNLP", "TPAMI", "TIP", "IJCV", "JMLR", "TMLR"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " is required")
    return value.strip()


def _url(value, label):
    value = _text(value, label)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(label + " must be an HTTPS source URL")
    return value


def _json_file(project_id, relative, value):
    path = ws._relative_path(project_id, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError("Existing discovery artifact differs: " + relative)
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def start_from_request(project_id: str, domain: str, requirements: str = "", paper_count: int = 5,
                       lookback_days: int = 365, as_of: str = "2026-11-18") -> dict:
    """Create a literature-search task from a domain and requirements, with no invented paper seeds."""
    ws.project_root(project_id)
    domain = _text(domain, "domain")
    if len(domain) > 500 or not isinstance(requirements, str) or len(requirements) > 20000:
        raise ValueError("Domain/requirements are too long or invalid")
    requirements = requirements.strip()
    if type(paper_count) is not int or not 1 <= paper_count <= 20:
        raise ValueError("paper_count must be 1–20")
    if type(lookback_days) is not int or not 1 <= lookback_days <= 730:
        raise ValueError("lookback_days must be 1–730")
    try:
        endpoint = date.fromisoformat(as_of)
    except (ValueError, TypeError):
        raise ValueError("as_of must be an ISO calendar date") from None
    contract = {"domain": domain, "requirements": requirements.strip(), "paper_count": paper_count,
                "as_of": endpoint.isoformat(), "publication_date_from": (endpoint - timedelta(days=lookback_days)).isoformat(),
                "lookback_days": lookback_days, "publication_status": ["accepted", "published"],
                "allowed_venues": sorted(TOP_VENUES), "code_status": "verified",
                "mandatory_conditions": ["official top-venue publication/acceptance evidence within the date window",
                                         "accessible original paper or preprint", "accessible author-linked open-source repository with pinned commit and license"],
                "evidence_note": "Metadata declarations require primary-source verification by the controller; registration does not mean papers were read or experiments run."}
    with ws._db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT manifest FROM workflow_projects WHERE project_id=?", (project_id,)).fetchone()
        if row:
            manifest = json.loads(row[0])
            if manifest.get("discovery") != contract:
                raise ValueError("Existing project has a different request or is a paper-seeded project")
        else:
            manifest = {"schema_version": 1, "project_id": project_id, "title": domain,
                        "seed_papers": [], "stage": "discovery", "created_utc": now(), "evidence_kind": "research",
                        "mode": "conversation-led", "research_execution": "requires_approved_scope", "discovery": contract}
            db.execute("INSERT INTO workflow_projects VALUES (?,?)", (project_id, json.dumps(manifest, ensure_ascii=False)))
            ws._event(db, project_id, "domain_discovery_started", contract)
        ws._materialize_project(manifest)
        _json_file(project_id, "discovery/request.json", contract)
    artifact = ws.register_artifact(project_id, "discovery/request.json", "discovery_contract")
    task = ws.create_task(project_id, f"检索并核查{paper_count}篇近年顶会顶刊开源论文", "literature_search",
        instructions=(f"研究领域：{domain}。用户要求：{requirements}\n读取discovery/request.json。"
                      f"实际检索正式发表日期处于{contract['publication_date_from']}至{as_of}的{paper_count}篇论文，"
                      "每篇同时满足顶会/顶刊正式发表或主会接收、可读原稿、真正开源且有许可的作者代码。"
                      "使用官方proceedings、OpenReview、CVF、DOI/期刊页面核验venue和日期，原稿可用预印本；"
                      "GitHub核验作者关联、固定commit、license和实际代码，不接受coming soon或只有占位README。"
                      "记录官方venue URL及具体支持证据；arXiv首发时间与正式发表时间分开。"
                      "学习各论文结构与图表组织，并读取核心方法代码，形成有机制的突破口与最便宜反证实验。"
                      "用record_paper_candidates登记完整候选元数据，再将各论文交给独立screening代理。"
                      "不要伪造检索结果、已读原文、运行或SOTA；研究实验按用户真实授权范围执行。"),
        output_paths=["discovery/papers.json"], task_key="domain-literature-search")
    return {**ws.project_status(project_id), "discovery_task": task, "contract_artifact": artifact,
            "next_action": "delegate_literature_search"}


def record_paper_candidates(project_id: str, candidates: list[dict]) -> dict:
    """Validate a complete shortlist and create one independent screening task per paper."""
    board = ws.project_status(project_id)
    contract = board["manifest"].get("discovery")
    if not contract:
        raise ValueError("Use start_from_request before registering a domain shortlist")
    if not isinstance(candidates, list) or len(candidates) != contract["paper_count"]:
        raise ValueError("Supply exactly the requested number of papers")
    selected = []
    identifiers, titles, repositories = set(), set(), set()
    for original in candidates:
        if not isinstance(original, dict):
            raise ValueError("Each candidate must be a metadata object")
        item = dict(original)
        for field in ("paper_id", "title", "venue", "publication_date", "repository_commit", "license", "venue_evidence", "code_evidence"):
            item[field] = _text(item.get(field), field)
        for field in ("official_venue_url", "paper_url", "code_url"):
            item[field] = _url(item.get(field), field)
        venue_host = urlparse(item["official_venue_url"]).hostname
        if not any(venue_host == host or venue_host.endswith("." + host) for host in OFFICIAL_HOSTS):
            raise ValueError("Official venue evidence must come from proceedings, venue, publisher or DOI primary sources")
        if item["venue"].upper() not in contract["allowed_venues"]:
            raise ValueError("Venue is outside the configured top-venue shortlist")
        if item.get("publication_status") not in contract["publication_status"] or item.get("code_status") != "verified":
            raise ValueError("Require official accepted/published status and verified open-source code")
        try:
            published = date.fromisoformat(item["publication_date"])
        except ValueError:
            raise ValueError("Invalid publication_date") from None
        if not date.fromisoformat(contract["publication_date_from"]) <= published <= date.fromisoformat(contract["as_of"]):
            raise ValueError("Publication is outside the required date window")
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", item["repository_commit"]):
            raise ValueError("Pin a full immutable repository commit")
        if item["license"].lower() in {"unknown", "none", "unlicensed", "pending", "coming soon", "proprietary", "closed source", "all rights reserved", "no license", "not specified"}:
            raise ValueError("A verified open-source license is required")
        parsed = urlparse(item["code_url"])
        if parsed.hostname not in {"github.com", "gitlab.com", "codeberg.org"} or len(parsed.path.strip("/").split("/")) != 2:
            raise ValueError("Provide the repository root URL")
        identity = item["paper_id"].casefold()
        title = " ".join(item["title"].casefold().split())
        repository = item["code_url"].rstrip("/").casefold()
        if identity in identifiers or title in titles or repository in repositories:
            raise ValueError("Duplicate paper or repository in shortlist")
        identifiers.add(identity); titles.add(title); repositories.add(repository)
        selected.append(item)
    selected.sort(key=lambda item: item["paper_id"])
    payload = {"domain": contract["domain"], "criteria": contract, "papers": selected,
               "status": "metadata_registered_requires_independent_reading"}
    # Validate every paper before publishing any artifact or creating tasks.
    with ws._db() as db:
        db.execute("BEGIN IMMEDIATE")
        _json_file(project_id, "discovery/papers.json", payload)
    artifact = ws.register_artifact(project_id, "discovery/papers.json", "paper_shortlist")
    tasks = []
    for index, item in enumerate(selected, 1):
        task = ws.create_task(project_id, f"阅读与试验设计：{item['title']}", "screening",
            instructions=("读取discovery/papers.json和对应原稿、官方仓库。核实元数据，不将已登记声明当实际核验。\n"
                          + json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n阅读原文并检查核心代码与可运行入口；"
                          "记录机制、局限、相关工作查新、低成本复现/反证方案、可比SOTA范围和资源要求。"
                          "记录这篇论文的段落组织、方法图、数据图可学习之处，保留具体页/图号。"
                          "实验仅在真实用户授权及冻结评价范围内执行；小试结果与最终主张分别记录。"),
            output_paths=[f"candidates/paper-{index:02d}.md"], task_key="domain-paper-" + hashlib.sha256(item["paper_id"].encode()).hexdigest()[:24])
        tasks.append(task)
    return {"project_id": project_id, "artifact": artifact, "tasks": tasks, "papers": selected,
            "status": "metadata_registered_requires_independent_reading"}
