"""Check own GPU leases and unavailable-resource paths without allocating any GPU."""
import json
import time
import uuid
from harness import service
from harness.state import local_dir, record


def submit(script, kind, gpu=None):
    job_id = uuid.uuid4().hex
    spec = {"kind": kind, "timeout_seconds": 15, "memory_mb": 256}
    if gpu:
        spec["gpu"] = gpu
    record("job_submitted", {"job_id": job_id, "spec": spec, "parameters": {}}, "toolchain-scheduling")
    service._remote({"operation": "submit", "job_id": job_id, "script": script, "spec": spec})
    return job_id


def terminal(job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = service.get_job_status(job_id)
        if status["state"] not in ("queued", "starting", "waiting_resources", "running"):
            return status
        time.sleep(0.25)
    service.cancel_job(job_id)
    raise AssertionError("Scheduling acceptance deadline exceeded")


script = '''import importlib.util,json
from pathlib import Path
s=importlib.util.spec_from_file_location("own_snapshot","worker.py")
w=importlib.util.module_from_spec(s);s.loader.exec_module(w)
w.gpu_inventory=lambda:[{"index":99,"uuid":"GPU-harness-lease-unit-test","free_mb":9000,"utilization_percent":0}]
p={"memory_mb":512,"allowed_indices":[99]}
first,gpu=w.acquire_gpu(p)
assert first is not None
second,gpu=w.acquire_gpu(p)
assert second is None
first.close()
third,gpu=w.acquire_gpu(p)
assert third is not None
third.close()
Path("metrics.json").write_text(json.dumps({"metrics":{"exclusive_lease":1,"lease_released":1},"artifacts":[]}))
print("own lease exclusion and release passed")
'''
lease_id = submit(script, "synthetic_lease_test")
lease = terminal(lease_id)
assert lease["state"] == "completed", lease
lease_result = service.collect_job_results(lease_id)
assert lease_result["metrics"]["exclusive_lease"] == 1
policy = {"memory_mb": 768, "reserve_mb": 2048, "max_utilization_percent": 10, "allowed_indices": [999], "wait_seconds": 1}
unavailable_id = submit("raise AssertionError('must never launch')", "synthetic_resource_wait_test", policy)
unavailable = terminal(unavailable_id)
assert unavailable["state"] == "resource_unavailable" and "child_pid" not in unavailable
unavailable_result = service.collect_job_results(unavailable_id)
assert unavailable_result["status"]["state"] == "resource_unavailable"
policy["wait_seconds"] = 25
cancel_id = submit("raise AssertionError('must never launch')", "synthetic_queue_cancel_test", policy)
service.cancel_job(cancel_id)
cancelled = terminal(cancel_id)
assert cancelled["state"] == "cancelled" and "child_pid" not in cancelled
result = {"status": "passed", "gpu_allocation": False, "exclusive_lease": lease_result,
          "unavailable": unavailable_result, "cancel_waiting": cancelled}
(local_dir() / "gpu-scheduling-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({"status": "passed", "lease_job": lease_id, "resource_unavailable_job": unavailable_id, "cancel_waiting_job": cancel_id}))
