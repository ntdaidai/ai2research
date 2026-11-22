"""Read-only, bounded CPU/affinity diagnostics for the actual worker child."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import remote_worker


@unittest.skipUnless(sys.platform == "linux", "Linux /proc resource samples")
class WorkerResourceTests(unittest.TestCase):
    def test_live_process_sample_is_read_only_and_missing_process_is_tolerated(self):
        before = os.sched_getaffinity(0)
        sample = remote_worker.child_resource_sample(os.getpid())
        self.assertEqual(sample["state"], "sampled")
        self.assertEqual(sample["pid"], os.getpid())
        for field in ("user_cpu_seconds", "system_cpu_seconds", "minor_faults", "major_faults"):
            self.assertGreaterEqual(sample[field], 0)
        self.assertTrue(sample["thread_affinity_counts"])
        self.assertIn("not exact exit-time", sample["precision"])
        self.assertEqual(os.sched_getaffinity(0), before)
        missing = remote_worker.child_resource_sample(999999999)
        self.assertEqual(missing["state"], "unavailable")

    def test_missing_proc_preserves_last_sample_and_numa_uses_observed_baseline(self):
        phase = {}
        initial = {"state": "sampled", "pid": 1, "leader_numa_pages_migrated": 2400000}
        later = {"state": "sampled", "pid": 1, "leader_numa_pages_migrated": 2400012}
        missing = {"state": "unavailable", "error_type": "FileNotFoundError"}
        with patch.object(remote_worker, "child_resource_sample", side_effect=[initial, later, missing]):
            remote_worker.update_child_resources(phase, 1)
            self.assertEqual(phase["resource_sample"]["leader_numa_pages_migrated_since_first_sample"], 0)
            remote_worker.update_child_resources(phase, 1)
            self.assertEqual(phase["resource_sample"]["leader_numa_pages_migrated_since_first_sample"], 12)
            remote_worker.update_child_resources(phase, 1)
            self.assertEqual(phase["resource_sample"], later)
            self.assertEqual(phase["resource_sample_unavailable"], missing)

    def test_real_two_phase_cpu_worker_records_child_counters_and_affinity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "spec.json").write_text(json.dumps({"kind": "synthetic_workflow_test", "timeout_seconds": 10,
                "memory_mb": 512, "python": sys.executable, "evaluator": "evaluate.py", "input_sha256": {
                    "evaluate.py": hashlib.sha256(b"import time; time.sleep(.4)").hexdigest()}}))
            (root / "script.py").write_text("import time; time.sleep(.4)")
            (root / "evaluate.py").write_bytes(b"import time; time.sleep(.4)")
            program = "from pathlib import Path; from tools.harness.remote_worker import supervise; supervise(Path(" + repr(folder) + "))"
            process = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            status = json.loads((root / "status.json").read_text())
            self.assertEqual(status["state"], "completed")
            for phase in ("running", "evaluating"):
                sample = status["phase_history"][phase]["resource_sample"]
                self.assertEqual(sample["state"], "sampled")
                self.assertLessEqual(sample["sampled_unix"], status["phase_history"][phase]["finished_unix"])
                self.assertGreaterEqual(sample["system_cpu_seconds"], 0)
                self.assertTrue(sample["thread_affinity_counts"])


if __name__ == "__main__":
    unittest.main()
