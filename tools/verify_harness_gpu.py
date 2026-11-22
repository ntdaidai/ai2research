"""GPU acceptance only: bounded driver kernels, state/result recovery and MLflow."""
import json
import time
from harness import service
from harness.state import local_dir


def finish(job_id):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        status = service.get_job_status(job_id)
        if status["state"] not in ("queued", "starting", "waiting_resources", "running"):
            if status["state"] != "completed":
                print(json.dumps(service.read_job_logs(job_id)))
                raise AssertionError(status)
            return service.collect_job_results(job_id)
        time.sleep(0.5)
    service.cancel_job(job_id)
    raise AssertionError("GPU smoke deadline exceeded")


results = []
for increment in (1, 2):
    submitted = service.submit_gpu_smoke(increment)
    print(json.dumps({"submitted": submitted, "increment": increment}), flush=True)
    result = finish(submitted["job_id"])
    assert result["metrics"]["gpu_verified"] == 1
    assert result["metrics"]["max_abs_error"] == 0
    assert result["metrics"]["elements_checked"] == 4096
    results.append(result)
assert results[1]["metrics"]["result_sum"] - results[0]["metrics"]["result_sum"] == 4096
again = service.collect_job_results(results[0]["job_id"])
assert again["already_collected"] and again["mlflow_run_id"] == results[0]["mlflow_run_id"]
comparison = service.compare_runs("gpu-toolchain-smoke")
assert all(r["mlflow_run_id"] in [x["run_id"] for x in comparison["runs"]] for r in results)
summary = {"status": "passed", "evidence_kind": "synthetic_gpu_toolchain_test", "jobs": results,
           "configuration_change_verified": True, "idempotent_collection": True, "comparison": comparison}
(local_dir() / "gpu-acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": "passed", "jobs": [{"job_id": r["job_id"], "gpu": r["status"]["gpu"]["index"],
       "metrics": r["metrics"], "mlflow_run_id": r["mlflow_run_id"]} for r in results]}, indent=2))
