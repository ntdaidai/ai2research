"""User-authorized synthetic workflow test; never a research campaign approval."""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from harness import service
from harness.experiments import approve_proposal
from harness.state import ROOT, local_dir

project = "toolchain-e2e-" + uuid.uuid4().hex[:8]
folder = local_dir() / "fixtures" / project
folder.mkdir(parents=True)
base = (ROOT / "tools/harness/cuda_smoke.py").read_text(encoding="utf-8")
base = base.replace('Path("sample.json").write_text', 'Path("predictions.json").write_text(json.dumps(list(values)))\n        Path("sample.json").write_text')
evaluator = '''import json
from pathlib import Path
config=json.loads(Path("config.json").read_text())
values=json.loads(Path("predictions.json").read_text())
assert len(values)==4096
target=config["target_increment"]
errors=[(value-(index+target))**2 for index,value in enumerate(values)]
result={"metrics":{"mse":sum(errors)/len(errors),"n_evaluated":len(errors)},"artifacts":["predictions.json","sample.json"],"evidence_kind":"synthetic_workflow_test"}
Path("metrics.json").write_text(json.dumps(result))
print(json.dumps(result),flush=True)
'''
(folder / "evaluate.py").write_text(evaluator, encoding="utf-8")
script = folder / "method.py"
args = {"project_id": project, "title": "Synthetic workflow baseline", "script_path": str(script),
        "parameters": {"target_increment": 2, "dataset": "synthetic_uint32_4096"}, "input_files": ["evaluate.py"],
        "evaluation_files": ["evaluate.py"], "evaluator_path": "evaluate.py", "purpose": "toolchain_test",
        "gpu_memory_mb": 768, "host_memory_mb": 2048, "timeout_seconds": 30, "wait_seconds": 30}


def prepare(source, title, parent=""):
    script.write_text(source, encoding="utf-8")
    proposal = service.prepare_experiment(**{**args, "title": title, "parent_proposal_id": parent})
    return proposal["proposal_id"]


def wait(job_id):
    deadline = time.monotonic() + 80
    while time.monotonic() < deadline:
        status = service.get_job_status(job_id)
        if status["state"] not in ("queued", "starting", "waiting_resources", "running", "evaluating"):
            return status
        time.sleep(0.3)
    service.cancel_job(job_id)
    raise AssertionError("End-to-end test deadline exceeded")


baseline_source = base.replace('amount = int(config.get("increment", 1))', 'amount = 1')
variant_source = base.replace('amount = int(config.get("increment", 1))', 'amount = 2')
root = prepare(baseline_source, "Synthetic workflow baseline")
approval = approve_proposal(root, "继续测试，完善这个流程", max_runs=4, allow_code_iterations=True)
approval_id = approval["event_id"]
baseline = service.submit_experiment(project, approval_id=approval_id, proposal_id=root)["job_id"]
print(json.dumps({"baseline_job": baseline, "project_id": project}), flush=True)
assert wait(baseline)["state"] == "completed"
first = service.collect_job_results(baseline)
assert first["metrics"]["mse"] == 1 and first["status"]["metrics_authority"] == "evaluate.py"
assert first["status"]["job_kind"] == "synthetic_workflow_test"

variant_plan = prepare(variant_source, "Synthetic code variant", root)
candidate = service.submit_experiment(project, approval_id=approval_id, proposal_id=variant_plan)["job_id"]
assert service.retry_job_dispatch(candidate)["state"] == "already_submitted"
assert wait(candidate)["state"] == "completed"
# Simulate another chat: a fresh process discovers and collects the same jobs.
fresh = subprocess.run([sys.executable, str(ROOT / "tools/harness_cli.py"), "resume_project", json.dumps({"project_id": project})],
                       capture_output=True, timeout=90)
assert fresh.returncode == 0, fresh.stderr.decode(errors="replace")
resumed = json.loads(fresh.stdout.decode("utf-8"))
second = service.collect_job_results(candidate)
assert second["already_collected"] and second["metrics"]["mse"] == 0
comparison = service.compare_jobs(baseline, candidate, "mse")
assert comparison["decision"] == "improved" and comparison["improvement"] == 1

failed_plan = prepare("raise RuntimeError('intentional infrastructure failure')", "Synthetic intentional failure", root)
failed = service.submit_experiment(project, approval_id=approval_id, proposal_id=failed_plan)["job_id"]
assert wait(failed)["state"] == "failed"
failure = service.collect_job_results(failed)
assert failure["metrics_error"] and failure["metrics"] == {}

tamper_plan = prepare("from pathlib import Path\nPath('evaluate.py').write_text('# intentional tamper test')", "Synthetic evaluator tampering", root)
tampered = service.submit_experiment(project, approval_id=approval_id, proposal_id=tamper_plan)["job_id"]
assert wait(tampered)["state"] == "failed"
try:
    service.collect_job_results(tampered)
except ValueError as exc:
    assert "input changed" in str(exc).lower()
    assert not (local_dir() / "jobs" / tampered).exists()
else:
    raise AssertionError("Tampered evaluator was accepted")
try:
    service.submit_experiment(project, approval_id=approval_id, proposal_id=variant_plan)
except ValueError as exc:
    assert "exhausted" in str(exc)
else:
    raise AssertionError("Test campaign exceeded its four-run budget")
final = service.resume_project(project)
assert final["run_budgets"][0]["used"] == 4 and final["run_budgets"][0]["remaining"] == 0
assert any(j["job_id"] == tampered and j["state"] == "needs_attention" for j in final["jobs"])
report = service.export_project_report(project, "mse")
assert report["valid_trials"] == 2 and report["source_rows"] == 4 and report["best_job_id"] == candidate
result = {"status": "passed", "purpose": "toolchain_test", "project_id": project, "approval_id": approval_id,
          "baseline": first, "candidate": second, "failure": failure, "tampered_job": tampered,
          "comparison": comparison, "resume": final, "report": report, "fresh_process_resume": True}
(local_dir() / "workflow-e2e-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": "passed", "project_id": project, "baseline_mse": 1, "candidate_mse": 0,
                  "report": report["report"], "graph": report["graph"]}, ensure_ascii=False))
