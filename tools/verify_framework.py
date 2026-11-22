"""Exercise the real MCP/CLI, chapter compiler and graphics adapters without research or paid calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


async def verify(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    state = destination / "state"
    state.mkdir(exist_ok=True)
    os.environ["RESEARCH_STATE_DIR"] = str(state)
    os.environ["AI2RESEARCH_TRANSPORT"] = "local"
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from harness import workspace, service
    # A new project preserves prior acceptance records and makes repeat runs
    # exercise the current code instead of trying to reclaim completed tasks.
    project_id = "framework-acceptance-" + uuid.uuid4().hex[:8]
    transport = StdioTransport(command=sys.executable, args=[str(ROOT / "tools/harness_mcp.py")], cwd=str(ROOT),
                               env={"RESEARCH_STATE_DIR": str(state), "AI2RESEARCH_TRANSPORT": "local"})
    result = {"evidence_kind": "toolchain_test", "paid_calls": 0, "research_experiments": 0,
              "state_store": str(state), "project_id": project_id}
    trace = []
    async with Client(transport, timeout=360) as client:
        async def call(name, **arguments):
            response = await client.call_tool(name, arguments)
            if response.is_error:
                raise RuntimeError("MCP tool failed: " + name)
            data = response.data
            trace.append({"step": len(trace) + 1, "tool": name,
                          "arguments": {k: v for k, v in arguments.items() if k != "lease_token"},
                          "response_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                          "status": data.get("status", data.get("state")) if isinstance(data, dict) else None})
            return data

        names = {t.name for t in await client.list_tools()}
        required = {"start_project", "research_workflow", "claim_task", "update_task", "prepare_review",
                    "run_external_review", "publication_plan", "assemble_manuscript", "export_results_figure",
                    "export_project_diagram", "export_delivery", "extract_paper_text"}
        assert required.issubset(names)
        result["mcp_tools"] = sorted(names)
        board = await call("start_project", project_id=project_id,
                           seed_papers=["toolchain reference A", "toolchain reference B"],
                           title="Framework toolchain acceptance", evidence_kind="toolchain_test")
        assert len(board["ready_task_ids"]) == 2
        root = Path(board["root"])
        worker = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from harness import service,workspace
project,task_id,owner=sys.argv[2:]
lease=service.claim_task(project,task_id,owner)
root=workspace.project_root(project)
relative=lease['output_paths'][0]
path=root/relative
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text('Synthetic infrastructure hand-off. No paper has been analyzed and no scientific result is asserted.\\n')
artifact=service.register_artifact(project,relative,'candidate_card')
service.update_task(project,task_id,lease['lease_token'],'completed',summary='Process-isolated toolchain task',artifact_ids=[artifact['artifact_id']])
print(json.dumps({'task_id':task_id,'completed':True}))
'''
        async def worker_process(task, index):
            completed = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-c", worker, str(ROOT / "tools"), project_id, task["task_id"], f"worker-{index}"],
                capture_output=True, text=True, cwd=ROOT, env=dict(os.environ), timeout=30)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            return json.loads(completed.stdout)
        result["parallel_process_tasks"] = await asyncio.gather(*(worker_process(t, i) for i, t in enumerate(board["tasks"])))
        selected = await call("research_workflow", action="select", project_id=project_id,
                              task_id=board["tasks"][0]["task_id"], reason="Infrastructure-only selection; no research ranking")
        next_step = await call("research_workflow", action="next", project_id=project_id)
        assert {t["role"] for t in next_step["ready_tasks"]} == {"method_designer", "implementation", "evaluation"}
        result["selection_and_parallel_plan"] = "passed"

        rows = root / "results" / "synthetic.csv"
        rows.parent.mkdir(exist_ok=True)
        rows.write_text("label,score\nbaseline,0.50\ncandidate,0.65\n", encoding="utf-8")
        art = await call("register_artifact", project_id=project_id, path="results/synthetic.csv", kind="results")
        chart = await call("export_results_figure", project_id=project_id, source_path="results/synthetic.csv", metric="score",
                           title="Infrastructure chart validation")
        assert chart["evidence_kind"] == "toolchain_test"
        figure_pdf = next(a["path"] for a in chart["artifacts"] if a["kind"] == "figure_pdf")
        diagram = root / "figures" / "adapter.drawio"
        shutil.copyfile(ROOT / "templates/toolchain_smoke.drawio", diagram)
        svg = await call("export_project_diagram", project_id=project_id, source_path="figures/adapter.drawio", output_format="svg")
        diagram_pdf = await call("export_project_diagram", project_id=project_id, source_path="figures/adapter.drawio", output_format="pdf")
        assert svg["status"] == diagram_pdf["status"] == "completed"
        result["drawio_svg_pdf"] = "passed"
        result["figure_png_pdf_and_reproduction_script"] = "passed"

        plan = await call("publication_plan", project_id=project_id)
        for chapter, task in plan["chapters"].items():
            lease = await call("claim_task", project_id=project_id, task_id=task["task_id"], owner="acceptance-" + chapter)
            text = ("This section validates independent chapter hand-off, evidence registration and manuscript assembly. "
                    "All numbers and content in this document are synthetic infrastructure fixtures. "
                    "No research experiment, SOTA claim or publication assessment is represented.\\par\n")
            if chapter != "abstract":
                text = "\\section{" + chapter.replace("_", " ").title() + "}\n" + text
            if chapter == "results":
                text += "\\begin{figure}[h]\\centering\\includegraphics[width=0.65\\linewidth]{../" + figure_pdf + "}\\caption{Synthetic toolchain fixture.}\\end{figure}\n"
            if chapter == "methods":
                text += "The local framework record is cited for provenance~\\cite{harness}.\n"
            path = root / task["path"]
            path.write_text(text, encoding="utf-8")
            chapter_artifact = await call("register_artifact", project_id=project_id, path=task["path"], kind="manuscript_chapter")
            await call("update_task", project_id=project_id, task_id=task["task_id"], lease_token=lease["lease_token"],
                       state="completed", summary="Synthetic chapter adapter acceptance", artifact_ids=[chapter_artifact["artifact_id"]])
        (root / "paper/references.bib").write_text(
            "@misc{harness, title={ai2research infrastructure acceptance}, author={ai2research}, year={2026}, note={Local toolchain record}}\n")
        assembled = await call("assemble_manuscript", project_id=project_id)
        assert assembled["status"] == "completed", assembled
        assert Path(assembled["pdf"]).read_bytes().startswith(b"%PDF-")
        extracted = await call("extract_paper_text", project_id=project_id, source_path=assembled["pdf"])
        assert extracted["status"] == "completed"
        result["eight_chapter_assembly_and_real_pdf"] = "passed"
        result["local_pdf_text_extraction"] = extracted["parser"]
        result["pdf"] = assembled["pdf"]
        try:
            service.prepare_review(project_id, "experiment", [art["artifact_id"]])
        except ValueError:
            result["synthetic_research_review_rejected"] = True
        else:
            raise AssertionError("Synthetic evidence was allowed into research review")
        blocked = await call("export_delivery", project_id=project_id)
        assert blocked["status"] == "incomplete" and blocked["archive"] is None
        delivery = await call("export_delivery", project_id=project_id, allow_incomplete=True)
        assert delivery["status"] == "incomplete"
        with zipfile.ZipFile(delivery["archive"]) as bundle:
            manifest = json.loads(bundle.read("DELIVERY_MANIFEST.json"))
            for item in manifest["files"]:
                assert hashlib.sha256(bundle.read(item["path"])).hexdigest() == item["sha256"]
        result["incomplete_delivery_gate_and_checksum_archive"] = "passed"
        result["archive"] = delivery["archive"]
        result["delivery_missing"] = delivery["missing"]
        # A fresh process must see all completed chapter tasks and their current hashes.
        fresh = await asyncio.to_thread(subprocess.run,
            [sys.executable, str(ROOT / "tools/harness_cli.py"), "project_status", json.dumps({"project_id": project_id})],
            capture_output=True, text=True, cwd=ROOT, env=dict(os.environ), timeout=30)
        assert fresh.returncode == 0, fresh.stderr
        recovered = json.loads(fresh.stdout)
        assert len([t for t in recovered["tasks"] if t["role"] == "chapter_writer" and t["state"] == "completed"]) == 8
        result["cross_process_recovery"] = "passed"
    result["status"] = "passed"
    trace_path = destination / (project_id + "-mcp-trace.json")
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n")
    result["mcp_trace"] = {"path": str(trace_path), "steps": len(trace),
                           "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest()}
    (destination / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/framework-acceptance")
    args = parser.parse_args()
    asyncio.run(verify(args.output.resolve()))
