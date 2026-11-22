"""Live lifecycle acceptance for two bounded CPU-only framework jobs."""
import json
import time
import uuid
from harness import service
from harness.state import local_dir, record


def submit(script, timeout):
    job_id = uuid.uuid4().hex
    result = service._remote({"operation": "submit", "job_id": job_id, "script": script,
                              "spec": {"kind": "toolchain_lifecycle_test", "timeout_seconds": timeout, "memory_mb": 256}})
    record("job_submitted", result)
    return job_id


timeout_id = submit("import time\nprint('timeout-test',flush=True)\ntime.sleep(10)\n", 2)
cancel_id = submit("import time\nprint('cancel-test',flush=True)\ntime.sleep(15)\n", 20)
cancel_requested = service.cancel_job(cancel_id)
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    timeout_status = service.get_job_status(timeout_id)
    cancel_status = service.get_job_status(cancel_id)
    if timeout_status["state"] == "timeout" and cancel_status["state"] == "cancelled":
        break
    time.sleep(0.5)
assert timeout_status["state"] == "timeout", timeout_status
assert cancel_status["state"] == "cancelled", cancel_status
result = {"timeout": timeout_status, "cancel": cancel_status, "status": "passed", "gpu_used": False}
(local_dir() / "remote-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
