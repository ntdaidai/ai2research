"""Python 3.8+ supervisor; only own jobs under the configured user workspace.

GPU leases coordinate this harness only, not other laboratory users. Memory limits
for GPU processes are sampled, not hardware isolation or an arbitrary-code sandbox.
"""
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home().resolve()
BASE = HOME / ".research-harness"
ACTIVE = ("queued", "starting", "waiting_resources", "running", "evaluating")


def base(create=False):
    if HOME.resolve() != HOME or Path.home().resolve() != HOME:
        raise ValueError("Unexpected remote user/home")
    if BASE.resolve() != BASE:
        raise ValueError("Workspace must not be a symlink")
    if create:
        BASE.mkdir(mode=0o700, exist_ok=True)
    return BASE


def job_path(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid job id")
    path = base() / "jobs" / job_id
    if path.resolve() != path:
        raise ValueError("Invalid job path")
    return path


def write_json(path, payload):
    if path.resolve() != path:
        raise ValueError("Output must not be a symlink")
    tmp = path.with_name(path.name + "." + str(os.getpid()) + "." + str(time.time_ns()) + ".tmp")
    with tmp.open("x", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=False)
    tmp.replace(path)


def safe_file(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Unsafe relative file")
    result = root / relative
    if result.resolve() != result or root not in result.parents:
        raise ValueError("File escapes job directory or is a symlink")
    return result


def gpu_inventory():
    result = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,pci.bus_id,name,memory.total,memory.free,utilization.gpu", "--format=csv,noheader,nounits"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    if result.returncode:
        raise ValueError("nvidia-smi inventory unavailable")
    return parse_inventory(result.stdout)


def parse_inventory(text):
    result = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if not row:
            continue
        index, gpu_uuid, pci, name, total, free, utilization = [x.strip() for x in row]
        result.append({"index": int(index), "uuid": gpu_uuid, "pci_bus_id": pci, "name": name,
                       "total_mb": int(total), "free_mb": int(free), "utilization_percent": int(utilization)})
    return result


def eligible_gpus(inventory, policy):
    required = policy["memory_mb"] + policy.get("reserve_mb", 1024)
    allowed = policy.get("allowed_indices", [])
    return sorted([g for g in inventory if (not allowed or g["index"] in allowed)
                   and g["free_mb"] >= required and g["utilization_percent"] <= policy.get("max_utilization_percent", 10)],
                  key=lambda g: (-g["free_mb"], g["index"]))


def acquire_gpu(policy):
    import fcntl
    lock_dir = base() / "gpu-locks"
    if lock_dir.resolve() != lock_dir:
        raise ValueError("GPU lock directory must not be a symlink")
    lock_dir.mkdir(exist_ok=True)
    for gpu in eligible_gpus(gpu_inventory(), policy):
        lock_path = lock_dir / (gpu["uuid"] + ".lock")
        if lock_path.resolve() != lock_path:
            raise ValueError("GPU lease must not be a symlink")
        handle = lock_path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            continue
        # Repeat the resource check after taking our own lease.
        current = eligible_gpus(gpu_inventory(), policy)
        selected = next((g for g in current if g["uuid"] == gpu["uuid"]), None)
        if selected is not None:
            return handle, selected
        handle.close()
    return None, None


def limits(spec):
    import resource
    seconds = spec["timeout_seconds"]
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds + 1))
    if not spec.get("gpu"):
        memory = spec.get("memory_mb", 512) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    # CUDA reserves large virtual address ranges, so GPU workers use sampled RSS.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


def child_setup(spec, parent_pid):
    # Do not leave the immediate worker alive if its supervisor is killed.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set parent-death signal")
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)
    disable_thp = spec.get("disable_transparent_hugepages", False)
    if type(disable_thp) is not bool:
        raise ValueError("disable_transparent_hugepages must be a boolean")
    if disable_thp:
        # Linux PR_SET_THP_DISABLE/PR_GET_THP_DISABLE. This flag applies only
        # to this process and is inherited across exec; system THP is untouched.
        try:
            if libc.prctl(41, 1, 0, 0, 0) != 0 or libc.prctl(42, 0, 0, 0, 0) != 1:
                raise OSError(ctypes.get_errno(), "Cannot enforce requested process-local THP disable")
        except Exception:
            os.write(2, b"harness child setup failed: mandatory process-local transparent hugepage disable was not applied; no fallback\n")
            raise
    limits(spec)


def verify_frozen_inputs(path, spec):
    expected = dict(spec.get("input_sha256", {}))
    if spec.get("script_sha256"):
        expected["script.py"] = spec["script_sha256"]
    for name, digest in expected.items():
        if hashlib.sha256(safe_file(path, name).read_bytes()).hexdigest() != digest:
            raise ValueError("Submitted input changed: " + name)
    if spec.get("config_sha256"):
        document = json.loads(safe_file(path, "config.json").read_text())
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if digest != spec["config_sha256"]:
            raise ValueError("Submitted configuration changed")


def kill_child(child):
    if child is not None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def own_process_usage(pgid):
    result = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError("Cannot monitor own GPU memory")
    gpu_mb = 0
    for row in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        try:
            pid, memory = int(row[0]), int(row[1])
            if os.getpgid(pid) == pgid:
                gpu_mb += memory
        except (ProcessLookupError, PermissionError, ValueError, IndexError):
            continue
    host_mb = 0
    # Only read RSS for processes in this job's group.
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if os.getpgid(int(proc.name)) != pgid:
                continue
            for line in (proc / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    host_mb += int(line.split()[1]) / 1024
        except (ProcessLookupError, PermissionError, FileNotFoundError):
            pass
    return gpu_mb, host_mb


def child_resource_sample(pid):
    """Read the immediate owned child's counters; never change affinity or policy.

    /proc stat aggregates this process's threads, not its subprocess tree. Sched
    NUMA counters are leader-task counters and may have an inherited baseline.
    The last live snapshot is not an exact exit-time resource total.
    """
    sample = {"pid": pid, "sampled_unix": time.time(), "state": "unavailable",
              "scope": "immediate_child_process_threads; excludes child subprocesses and monitoring helpers",
              "precision": "last live sample, not exact exit-time totals"}
    try:
        root = Path("/proc") / str(pid)
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        # stat fields 3 onward: minflt=10, majflt=12, utime=14, stime=15.
        ticks = os.sysconf("SC_CLK_TCK")
        sample.update(state="sampled", user_cpu_seconds=int(stat[11])/ticks,
                      system_cpu_seconds=int(stat[12])/ticks,
                      minor_faults=int(stat[7]), major_faults=int(stat[9]))
        affinity_counts = {}
        missing_threads = 0
        try:
            for task in (root / "task").iterdir():
                try:
                    cpus = ",".join(str(cpu) for cpu in sorted(os.sched_getaffinity(int(task.name))))
                    affinity_counts[cpus] = affinity_counts.get(cpus, 0) + 1
                except (ProcessLookupError, PermissionError, OSError):
                    missing_threads += 1
            sample["thread_affinity_counts"] = affinity_counts
            sample["unavailable_thread_affinities"] = missing_threads
        except OSError as exc:
            sample["affinity_state"] = "unavailable"
            sample["affinity_error_type"] = type(exc).__name__
        try:
            for line in (root / "status").read_text().splitlines():
                if line.startswith("THP_enabled:"):
                    sample["THP_enabled"] = int(line.split(":", 1)[1].strip())
                    break
            else:
                sample["thp_state"] = "unavailable"
        except (OSError, ValueError) as exc:
            sample["thp_state"] = "unavailable"
            sample["thp_error_type"] = type(exc).__name__
        try:
            for line in (root / "sched").read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "numa_pages_migrated":
                    sample["leader_numa_pages_migrated"] = int(value.strip())
                    break
            else:
                sample["numa_counter_state"] = "unavailable"
        except (OSError, ValueError) as exc:
            sample["numa_counter_state"] = "unavailable"
            sample["numa_counter_error_type"] = type(exc).__name__
    except (OSError, ValueError, IndexError) as exc:
        sample.update(state="unavailable", error_type=type(exc).__name__)
    return sample


def update_child_resources(phase_stats, pid):
    """Keep the last successful sample; expose read failures without failing research."""
    sample = child_resource_sample(pid)
    if sample["state"] != "sampled":
        phase_stats["resource_sample_unavailable"] = sample
        return
    counter = sample.get("leader_numa_pages_migrated")
    if counter is not None:
        baseline = phase_stats.setdefault("leader_numa_pages_migrated_baseline", counter)
        sample["leader_numa_pages_migrated_since_first_sample"] = max(0, counter-baseline)
    phase_stats["resource_sample"] = sample


def exit_diagnostic(returncode, spec):
    """Explain observed exit signals without guessing OOM from SIGKILL."""
    if returncode == -signal.SIGXCPU:
        return {'failure_code': 'process_cpu_limit',
                'reason': 'SIGXCPU: per-process CPU time limit exhausted; CPU seconds sum all threads, not wall time. Configured limit: '
                          + str(spec['timeout_seconds']) + ' seconds per process per phase.'}
    if returncode == -signal.SIGXFSZ:
        return {'failure_code': 'file_size_limit',
                'reason': 'SIGXFSZ: a single output file exceeded the 16 MiB limit. Shard large checkpoints inside the job.'}
    if returncode is not None and returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = str(-returncode)
        return {'failure_code': 'process_signal', 'signal': name,
                'reason': 'Child terminated by ' + name + '; cause requires evidence. SIGKILL alone does not establish OOM.'}
    return {'failure_code': 'process_exit', 'reason': 'Child exited with code ' + str(returncode)}


def supervise(path):
    spec = json.loads((path / "spec.json").read_text())
    status = {"job_id": path.name, "state": "starting", "pid": os.getpid(), "started_unix": time.time(),
              "job_kind": spec["kind"], "peak_gpu_memory_mb": 0, "peak_host_rss_mb": 0,
              "resource_wait_seconds": 0., "phase_history": {},
              "limit_semantics": {"wall_timeout_seconds": spec['timeout_seconds'],
                                  "wall_shared_across_phases": True,
                                  "cpu_limit_seconds_per_process_per_phase": spec['timeout_seconds'],
                                  "cpu_counts_all_threads": True}}
    child = None
    lease = None
    waiting_started = time.monotonic()
    lease_started = None
    execution_started = None
    phase_started = None

    def finish_phase():
        nonlocal phase_started
        if phase_started is not None:
            status['phase_history'][status['phase']].update(
                finished_unix=time.time(), elapsed_wall_seconds=time.monotonic()-phase_started,
                exit_code=child.returncode if child is not None else None)
            phase_started = None

    def terminate(signum, frame):
        kill_child(child)
        status.update(state="cancelled", finished_unix=time.time())
        write_json(path / "status.json", status)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    write_json(path / "status.json", status)
    try:
        env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ("TOKEN", "API_KEY", "SECRET"))}
        env.update({"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                    "PYTHONUNBUFFERED": "1", "XDG_CACHE_HOME": str(path / "cache"), "CUDA_CACHE_PATH": str(path / "cuda-cache")})
        if spec.get("gpu"):
            deadline = time.monotonic() + spec["gpu"].get("wait_seconds", 60)
            status["state"] = "waiting_resources"
            write_json(path / "status.json", status)
            while True:
                if (path / "cancel.request").exists():
                    terminate(signal.SIGTERM, None)
                lease, gpu = acquire_gpu(spec["gpu"])
                if gpu is not None:
                    break
                if time.monotonic() >= deadline:
                    status.update(state="resource_unavailable", reason="No GPU satisfies free-memory/utilization/own-lease policy")
                    return
                time.sleep(1)
            status["gpu"] = gpu
            lease_started = time.monotonic()
            status.update(gpu_acquired_unix=time.time(), resource_wait_seconds=lease_started-waiting_started)
            env.update({"CUDA_VISIBLE_DEVICES": gpu["uuid"], "HARNESS_GPU_UUID": gpu["uuid"], "HARNESS_GPU_PCI_BUS_ID": gpu["pci_bus_id"]})
        if (path / "cancel.request").exists():
            terminate(signal.SIGTERM, None)
        execution_started = time.monotonic()
        deadline = execution_started + spec["timeout_seconds"]
        phases = [("running", "script.py", "run.log")]
        if spec.get("evaluator"):
            if spec["evaluator"] not in spec.get("input_sha256", {}):
                raise ValueError("Evaluator must be a frozen input file")
            phases.append(("evaluating", spec["evaluator"], "evaluate.log"))
        for phase, filename, log_name in phases:
            verify_frozen_inputs(path, spec)
            if phase == "evaluating":
                # Editable method code is not the authority for final metrics.
                metrics_file = safe_file(path, "metrics.json")
                if metrics_file.exists():
                    metrics_file.unlink()
            phase_env = dict(env)
            if phase == "evaluating" and not spec.get("evaluation_on_gpu", False):
                phase_env["CUDA_VISIBLE_DEVICES"] = ""
            status.update(state=phase, phase=phase)
            phase_started = time.monotonic()
            status['phase_history'][phase] = {'started_unix': time.time()}
            last_status_write = phase_started
            write_json(path / "status.json", status)
            with safe_file(path, log_name).open("wb") as log:
                parent_pid = os.getpid()
                child = subprocess.Popen([spec.get("python", sys.executable), "-u", filename], cwd=str(path), env=phase_env,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                         preexec_fn=lambda: child_setup(spec, parent_pid))
                status["child_pid"] = child.pid
                update_child_resources(status['phase_history'][phase], child.pid)
                write_json(path / "status.json", status)
                while child.poll() is None:
                    if (path / "cancel.request").exists():
                        terminate(signal.SIGTERM, None)
                    if time.monotonic() >= deadline:
                        kill_child(child)
                        status["state"] = "timeout"
                        status.update(failure_code='wall_timeout', reason='Training and independent evaluation exhausted their shared wall-clock deadline.')
                        break
                    if spec.get("gpu"):
                        gpu_mb, host_mb = own_process_usage(child.pid)
                        status["peak_gpu_memory_mb"] = max(status["peak_gpu_memory_mb"], gpu_mb)
                        status["peak_host_rss_mb"] = max(status["peak_host_rss_mb"], host_mb)
                        if gpu_mb > spec["gpu"]["memory_mb"] or host_mb > spec["memory_mb"]:
                            kill_child(child)
                            status.update(state="resource_limit", reason="Sampled job GPU memory or host RSS exceeded approved budget")
                            status['failure_code'] = 'sampled_resource_limit'
                            break
                    if time.monotonic()-last_status_write >= 5:
                        status['phase_history'][phase]['elapsed_wall_seconds'] = time.monotonic()-phase_started
                        update_child_resources(status['phase_history'][phase], child.pid)
                        if lease_started is not None:
                            status['gpu_lease_seconds'] = time.monotonic()-lease_started
                        write_json(path / 'status.json', status)
                        last_status_write = time.monotonic()
                    time.sleep(0.25)
                status["exit_code"] = child.returncode
                finish_phase()
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if status["state"] in ("timeout", "resource_limit"):
                    break
                if child.returncode != 0:
                    status["state"] = "failed"
                    status.update(exit_diagnostic(child.returncode, spec))
                    break
                verify_frozen_inputs(path, spec)
                status["state"] = "completed"
                if phase == "evaluating":
                    status["metrics_authority"] = filename
    except Exception as exc:
        kill_child(child)
        status.update(state="failed", error=type(exc).__name__, message=str(exc)[:1000])
    finally:
        finish_phase()
        if execution_started is not None:
            status['execution_wall_seconds'] = time.monotonic()-execution_started
        if lease is not None:
            status['gpu_lease_seconds'] = time.monotonic()-lease_started
            lease.close()
        elif execution_started is None:
            status['resource_wait_seconds'] = time.monotonic()-waiting_started
        status["finished_unix"] = time.time()
        write_json(path / "status.json", status)


def collect_result(path, status):
    if status["state"] in ACTIVE:
        raise ValueError("Job is still active")
    names = ["spec.json", "script.py", "config.json", "status.json", "run.log", "evaluate.log", "metrics.json"]
    saved_spec = json.loads(safe_file(path, "spec.json").read_text())
    names.extend(saved_spec.get("input_sha256", {}).keys())
    metrics = {}
    metrics_path = safe_file(path, "metrics.json")
    metrics_error = None
    if metrics_path.exists():
        if metrics_path.stat().st_size > 1024 * 1024:
            raise ValueError("Metrics file too large")
        try:
            document = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics = document.get("metrics", {})
            if not isinstance(metrics, dict) or len(metrics) > 100 or any(not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for k, v in metrics.items()):
                raise ValueError("Metrics must be finite numeric key/value pairs")
            artifacts = document.get("artifacts", [])
            if not isinstance(artifacts, list) or len(artifacts) > 20 or any(not isinstance(x, str) for x in artifacts):
                raise ValueError("Invalid artifact list")
            names.extend(artifacts)
        except (ValueError, TypeError, AttributeError) as exc:
            metrics = {}
            metrics_error = str(exc)
    elif saved_spec.get("evaluator") or saved_spec["kind"] in ("approved_gpu_experiment", "synthetic_gpu_toolchain_test"):
        metrics_error = "metrics.json missing; no evaluation result"
    payloads = []
    total = 0
    for name in dict.fromkeys(names):
        source = safe_file(path, name)
        if not source.exists():
            continue
        if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Artifact not a bounded regular file")
        content = source.read_bytes()
        total += len(content)
        if total > 32 * 1024 * 1024:
            raise ValueError("Artifacts exceed collection budget")
        payloads.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                         "base64": base64.b64encode(content).decode()})
    return {"job_id": path.name, "status": status, "metrics": metrics, "metrics_error": metrics_error, "files": payloads}


def launch_job(path):
    import fcntl
    with safe_file(path, "launch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = json.loads(safe_file(path, "status.json").read_text())
        if status["state"] != "queued":
            return False
        launch = safe_file(path, "launch.json")
        if launch.exists():
            pid = json.loads(launch.read_text())["pid"]
            try:
                if str(path / "worker.py").encode() in (Path("/proc") / str(pid) / "cmdline").read_bytes():
                    return False
            except FileNotFoundError:
                pass
        with safe_file(path, "supervisor.log").open("ab") as log:
            proc = subprocess.Popen([sys.executable, str(path / "worker.py"), "run", path.name], stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        write_json(launch, {"pid": proc.pid, "launched_unix": time.time()})
        return True


def gateway(req):
    operation = req["operation"]
    if operation == "probe":
        inventory = gpu_inventory()
        return {"home": str(HOME), "python": sys.version.split()[0], "workspace_exists": base().exists(), "gpus": inventory,
                "torch_available": __import__("importlib.util", fromlist=["util"]).find_spec("torch") is not None}
    if operation == "submit":
        base(create=True)
        jobs = BASE / "jobs"
        if jobs.resolve() != jobs:
            raise ValueError("Jobs directory must not be a symlink")
        jobs.mkdir(exist_ok=True)
        path = job_path(req["job_id"])
        if path.exists():
            saved = json.loads((path / "spec.json").read_text())
            if saved != req["spec"] or (path / "script.py").read_text() != req["script"]:
                raise ValueError("Job ID already belongs to another payload")
            launched = launch_job(path)
            return {"job_id": path.name, "state": "submitted" if launched else "already_submitted", "remote_path": str(path)}
        final_path = path
        path = jobs / (".upload-" + req["job_id"] + "-" + str(time.time_ns()))
        path.mkdir(mode=0o700)
        spec = req["spec"]
        if not 1 <= spec["timeout_seconds"] <= 86400 or not 64 <= spec.get("memory_mb", 512) <= 65536:
            raise ValueError("Invalid worker resource bounds")
        write_json(path / "spec.json", spec)
        write_json(path / "config.json", req.get("config", {}))
        safe_file(path, "script.py").write_text(req["script"], encoding="utf-8")
        for item in req.get("files", []):
            if item["path"] in ("script.py", "config.json", "spec.json", "worker.py", "status.json"):
                raise ValueError("Reserved input filename")
            target = safe_file(path, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(item["base64"], validate=True)
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("Input digest mismatch")
            target.write_bytes(data)
        (path / "worker.py").write_bytes(base64.b64decode(req["worker_source"]))
        write_json(path / "status.json", {"job_id": req["job_id"], "state": "queued", "job_kind": spec["kind"]})
        path.rename(final_path)
        launch_job(final_path)
        return {"job_id": final_path.name, "state": "submitted", "remote_path": str(final_path)}
    path = job_path(req["job_id"])
    if not path.is_dir():
        raise ValueError("Job not found")
    status = json.loads(safe_file(path, "status.json").read_text())
    if operation == "cancel" and status["state"] in ACTIVE:
        safe_file(path, "cancel.request").touch()
    if operation in ("status", "cancel", "collect") and status["state"] in ACTIVE and "pid" in status:
        proc = Path("/proc") / str(status["pid"]) / "cmdline"
        try:
            alive = str(path / "worker.py").encode() in proc.read_bytes()
        except FileNotFoundError:
            alive = False
        if not alive:
            status.update(state="interrupted", finished_unix=time.time(), reason="Supervisor no longer exists")
            write_json(path / "status.json", status)
        elif operation == "cancel":
            os.kill(status["pid"], signal.SIGTERM)
            status["state"] = "cancellation_requested"
    if operation in ("status", "cancel"):
        return status
    if operation == "collect":
        return collect_result(path, status)
    if operation == "logs":
        log = safe_file(path, "run.log")
        tail = max(1, min(int(req.get("tail_bytes", 12000)), 64000))
        text = ""
        if log.exists():
            with log.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - tail))
                text = f.read(tail).decode("utf-8", errors="replace")
        return {"job_id": path.name, "log": text, "state": status["state"]}
    raise ValueError("Unknown operation")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        supervise(job_path(sys.argv[2]))
    else:
        try:
            request = json.loads(sys.stdin.buffer.read())
            print(json.dumps(gateway(request), ensure_ascii=False, allow_nan=False))
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
            sys.exit(1)
