"""Persist a survey and advance a bounded set of frozen research proposals."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid

from .experiments import compare_jobs, cycle_job_id, event, submit_proposal
from .recovery import ACTIVE
from .state import connect, now, record


async def research_cycle(action: str, project_id: str = "paper-research", cycle_id: str = "",
                         paper_query: str = "", code_query: str = "", evidence_id: str = "",
                         approval_id: str = "", candidate_proposal_ids: list[str] | None = None,
                         hypothesis: str = "", metric: str = "", direction: str = "minimize",
                         minimum_improvement: float = 0.0, target: float | None = None,
                         wait_seconds: int = 30) -> dict:
    """Discover evidence, register approved proposals, inspect, or run a resumable cycle."""
    from . import service

    if not re.fullmatch(r"[\w.-]{1,80}", project_id) or project_id in (".", ".."):
        raise ValueError("Invalid project_id")
    if action == "discover":
        if not paper_query.strip() or not code_query.strip():
            raise ValueError("Specify both a paper query and a code query")
        papers, code = await asyncio.gather(
            service.search_papers(paper_query, 5), asyncio.to_thread(service.find_code, code_query, 5),
            return_exceptions=True)
        if isinstance(papers, Exception) or papers.get("is_error") or papers.get("status") == "unavailable":
            first_error = str(papers)[:400]
            try:
                papers = await asyncio.to_thread(service._search_arxiv_atom, paper_query, 5)
                papers["fallback_reason"] = first_error
            except Exception as exc:
                papers = {"status": "error", "type": type(exc).__name__,
                          "message": str(exc)[:400], "mcp_error": first_error}
        sources = {}
        for name, result in (("papers", papers), ("code", code)):
            if isinstance(result, Exception):
                result = {"status": "error", "type": type(result).__name__, "message": str(result)[:500]}
            sources[name] = result
        failures = sum(bool(result.get("is_error") or result.get("status") in ("error", "unavailable"))
                       for result in sources.values())
        state = ("collected", "partial", "needs_attention")[failures]
        return record("research_evidence", {"paper_query": paper_query, "code_query": code_query,
                                           "state": state, **sources}, project_id)
    if action == "start":
        approval = event(approval_id, "experiment_approval")
        baseline = event(approval["proposal_id"], "experiment_proposal")
        candidates = candidate_proposal_ids or []
        proposals = [baseline["event_id"], *candidates]
        if (approval["project_id"] != project_id or not baseline["resources"].get("evaluator")
                or not approval["allow_code_iterations"] or len(proposals) > approval["max_runs"]
                or len(set(proposals)) != len(proposals)):
            raise ValueError("Use a matching bounded approval with a frozen evaluator and distinct proposals")
        if not hypothesis.strip() or not metric.strip() or direction not in ("minimize", "maximize"):
            raise ValueError("Specify a hypothesis, metric and valid comparison direction")
        if not math.isfinite(minimum_improvement) or minimum_improvement < 0 or (target is not None and not math.isfinite(target)):
            raise ValueError("Invalid comparison threshold or target")
        if evidence_id and event(evidence_id, "research_evidence")["project_id"] != project_id:
            raise ValueError("Evidence belongs to another project")
        for proposal_id in candidates:
            if event(proposal_id, "experiment_proposal")["project_id"] != project_id:
                raise ValueError("Candidate belongs to another project")
        payload = {"approval_id": approval_id, "proposals": proposals, "evidence_id": evidence_id,
                   "hypothesis": hypothesis, "metric": metric, "direction": direction,
                   "minimum_improvement": minimum_improvement, "target": target}
        cycle_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ai2research:approval:{approval_id}").hex
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT payload FROM events WHERE id=?", (cycle_id,)).fetchone()
            if existing and json.loads(existing["payload"]) != payload:
                raise ValueError("This approval already has a cycle; use enqueue to add candidates")
            if not existing:
                db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                           (cycle_id, project_id, "research_cycle", now(), json.dumps(payload)))
        return {**payload, "cycle_id": cycle_id, "state": "ready"}
    if action not in ("status", "advance", "run", "enqueue"):
        raise ValueError("Use discover, start, enqueue, status, advance or run")
    campaign = event(cycle_id, "research_cycle")
    if action == "enqueue":
        return _enqueue(campaign, candidate_proposal_ids or [])
    if not 0 <= wait_seconds <= 60:
        raise ValueError("wait_seconds must be between 0 and 60")
    deadline = time.monotonic() + wait_seconds
    while True:
        result = await asyncio.to_thread(_advance, campaign, action != "status")
        if action != "run" or result["state"] not in ("dispatched", "waiting", "evaluated"):
            return result
        if time.monotonic() >= deadline:
            return result
        if result["state"] in ("dispatched", "waiting"):
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))


def _queued(db, campaign):
    rows = db.execute("SELECT payload FROM events WHERE project=? AND kind='research_cycle_enqueue' ORDER BY rowid",
                      (campaign["project_id"],)).fetchall()
    additions = [json.loads(row["payload"]) for row in rows]
    return campaign["proposals"] + [p for item in additions if item["cycle_id"] == campaign["event_id"]
                                    for p in item["proposals"]]


def _enqueue(campaign, candidates):
    if not candidates:
        raise ValueError("Specify candidate_proposal_ids")
    approval = event(campaign["approval_id"], "experiment_approval")
    for proposal_id in candidates:
        if event(proposal_id, "experiment_proposal")["project_id"] != campaign["project_id"]:
            raise ValueError("Candidate belongs to another project")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        queue = _queued(db, campaign)
        additions = [p for p in dict.fromkeys(candidates) if p not in queue]
        if len(queue) + len(additions) > approval["max_runs"]:
            raise ValueError("Queue would exceed the approved run count")
        if additions:
            payload = {"cycle_id": campaign["event_id"], "proposals": additions}
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                       (uuid.uuid4().hex, campaign["project_id"], "research_cycle_enqueue", now(), json.dumps(payload)))
    return {"state": "queued", "cycle_id": campaign["event_id"], "added": additions, "proposals": queue + additions}


def _advance(campaign, execute):
    from . import service

    cycle_id = campaign["event_id"]
    project_id = campaign["project_id"]
    summary = {"cycle_id": cycle_id, "project_id": project_id, "metric": campaign["metric"], "decisions": []}
    with connect() as db:
        proposals = _queued(db, campaign)
        rows = db.execute("SELECT id, kind, payload FROM events WHERE project=? AND kind IN "
                          "('job_submitted','job_dispatch','job_dispatch_recovery','research_cycle_decision')",
                          (project_id,)).fetchall()
    registered, dispatched, decisions = set(), set(), {}
    for row in rows:
        payload = json.loads(row["payload"])
        if row["kind"] == "research_cycle_decision":
            decisions[row["id"]] = payload
        elif row["kind"] == "job_submitted":
            registered.add(payload["job_id"])
        else:
            dispatched.add(payload["job_id"])
    best = None
    for index, proposal_id in enumerate(proposals):
        job_id = cycle_job_id(cycle_id, proposal_id)
        decision_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ai2research:decision:{job_id}").hex
        saved = decisions.get(decision_id)
        step = {"job_id": job_id, "proposal_id": proposal_id}
        if saved is None:
            if not execute:
                return {**summary, "state": "in_progress" if job_id in registered else "ready", "next": step, "best": best}
            if job_id not in dispatched:
                submit_proposal(proposal_id, campaign["approval_id"], cycle_id=cycle_id)
                return {**summary, "state": "dispatched", "next": step, "best": best}
            status = service.get_job_status(job_id)
            if status["state"] in ACTIVE:
                return {**summary, "state": "waiting", "job_state": status["state"], "next": step, "best": best}
            collected = service.collect_job_results(job_id)
            metric = collected["metrics"].get(campaign["metric"])
            valid = (status["state"] == "completed" and not collected.get("metrics_error")
                     and isinstance(metric, (int, float)) and math.isfinite(metric))
            decision = "failed"
            if valid:
                decision = "baseline"
                if index:
                    comparison = compare_jobs(best["job_id"], job_id, campaign["metric"], campaign["direction"],
                                              campaign["minimum_improvement"])
                    decision = "keep" if comparison["decision"] == "improved" else "discard"
            saved = {**step, "decision": decision, "value": metric if valid else None,
                     "job_state": status["state"], "mlflow_run_id": collected["mlflow_run_id"]}
            with connect() as db:
                db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                           (decision_id, project_id, "research_cycle_decision", now(), json.dumps(saved)))
            summary["decisions"].append(saved)
            return {**summary, "state": "evaluated", "best": saved if decision in ("baseline", "keep") else best}
        summary["decisions"].append(saved)
        if index == 0 and saved["decision"] == "failed":
            return {**summary, "state": "baseline_failed", "best": None}
        if saved["decision"] in ("baseline", "keep"):
            best = saved
        if best and campaign["target"] is not None:
            target = campaign["target"]
            reached = best["value"] <= target if campaign["direction"] == "minimize" else best["value"] >= target
            if reached:
                return {**summary, "state": "target_reached", "best": best}
    return {**summary, "state": "queue_finished", "best": best}
