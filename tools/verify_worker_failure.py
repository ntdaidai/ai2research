"""Kill only a test child's own supervisor, then verify its parent-death cleanup."""
import hashlib
import json
import time
import uuid
from harness import service
from harness.state import local_dir, record

job_id = uuid.uuid4().hex
script = '''import os,signal,time,json
from pathlib import Path
print(json.dumps({"own_child_pid":os.getpid()}),flush=True)
os.kill(os.getppid(),signal.SIGKILL)
time.sleep(1)
Path("metrics.json").write_text(json.dumps({"metrics":{"orphan_survived":1}}))
'''
spec = {"kind": "synthetic_parent_death_test", "timeout_seconds": 10, "memory_mb": 256,
        "script_sha256": hashlib.sha256(script.encode()).hexdigest()}
record("job_submitted", {"job_id": job_id, "spec": spec, "parameters": {}}, "toolchain-worker-failure")
service._remote({"operation": "submit", "job_id": job_id, "spec": spec, "script": script})
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    status = service.get_job_status(job_id)
    if status["state"] == "interrupted":
        break
    time.sleep(0.2)
assert status["state"] == "interrupted", status
time.sleep(1.2)
result = service.collect_job_results(job_id)
assert "orphan_survived" not in result["metrics"]
assert "own_child_pid" in service.read_job_logs(job_id)["log"]
summary = {"status": "passed", "job_id": job_id, "gpu_allocation": False,
           "supervisor_loss_detected": True, "immediate_worker_stopped": True, "collection": result}
(local_dir() / "worker-failure-acceptance.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps({"status": "passed", "job_id": job_id, "immediate_worker_stopped": True}))
