import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from harness import collection, experiments, recovery, service, state


class WorkflowRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_invalid_bundle_does_not_overwrite_canonical_files(self):
        job_id = "a" * 32
        folder = Path(self.temp.name) / "jobs" / job_id
        folder.mkdir(parents=True)
        (folder / "script.py").write_text("trusted existing snapshot")
        def entry(name, raw, digest=None):
            return {"path": name, "bytes": len(raw), "base64": base64.b64encode(raw).decode(),
                    "sha256": digest or hashlib.sha256(raw).hexdigest()}
        payload = {"job_id": job_id, "status": {"job_id": job_id, "state": "completed"},
                   "files": [entry("script.py", b"changed script"), entry("metrics.json", b"{}", "0" * 64)]}
        state.record("job_submitted", {"job_id": job_id})
        with patch.object(service, "_remote", return_value=payload):
            with self.assertRaisesRegex(ValueError, "checksum"):
                experiments.collect_job_results(job_id)
        self.assertEqual((folder / "script.py").read_text(), "trusted existing snapshot")

    def test_uncertain_dispatch_reuses_job_and_budget(self):
        source = Path(self.temp.name) / "method.py"
        source.write_text("# never executed unit fixture")
        proposal = experiments.prepare_experiment("fixture", "fixture plan", str(source), purpose="toolchain_test")
        approval = experiments.approve_proposal(proposal["proposal_id"], "fixture approval", 1)
        with patch.object(service, "_remote", side_effect=TimeoutError("simulated lost reply")):
            with self.assertRaises(TimeoutError):
                experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])
        job_id = recovery.list_jobs("fixture")["jobs"][0]["job_id"]
        with patch.object(service, "_remote", return_value={"job_id": job_id, "state": "already_submitted"}) as remote:
            recovery.retry_job_dispatch(job_id)
            self.assertEqual(remote.call_args[0][0]["job_id"], job_id)
        self.assertEqual(len(recovery.list_jobs("fixture")["jobs"]), 1)
        with self.assertRaisesRegex(ValueError, "exhausted"):
            experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])

    def test_resuming_one_bad_job_does_not_hide_healthy_jobs(self):
        bad, good = "b" * 32, "c" * 32
        for job_id in (bad, good):
            state.record("job_submitted", {"job_id": job_id}, "fixture")
        def status(job_id):
            if job_id == bad:
                raise TimeoutError("simulated SSH interruption")
            return {"job_id": job_id, "state": "running"}
        with patch.object(service, "get_job_status", side_effect=status), patch.object(service, "collect_job_results") as collect:
            result = recovery.resume_project("fixture")
        self.assertEqual({j["state"] for j in result["jobs"]}, {"running", "needs_attention"})
        collect.assert_not_called()

    def test_resume_never_submits_new_experiment(self):
        job_id = "d" * 32
        state.record("job_submitted", {"job_id": job_id}, "fixture")
        with patch.object(service, "get_job_status", return_value={"state": "completed"}), \
             patch.object(service, "collect_job_results", return_value={"mlflow_run_id": "mock-run", "local_path": "fixture", "metrics_error": None}), \
             patch.object(service, "submit_experiment") as submit:
            recovery.resume_project("fixture")
        submit.assert_not_called()

    def test_external_review_error_does_not_leak_credentials_or_retry(self):
        fake = types.SimpleNamespace(suppress_debug_info=False)
        fake.completion = Mock(side_effect=RuntimeError("DO_NOT_LOG_TEST_CREDENTIAL"))
        settings = service.config()
        settings["review"].update(max_calls=1, server_max_calls=1)
        with patch.object(service, "config", return_value=settings), patch.object(state, "config", return_value=settings), \
             patch.object(service, "_api_key", return_value="unit-test-key"), patch.dict(sys.modules, {"litellm": fake}):
            result = service.review_topic("fixture, no paid provider contacted")
        self.assertEqual(fake.completion.call_count, 1)
        self.assertEqual(fake.completion.call_args.kwargs["num_retries"], 0)
        self.assertNotIn("DO_NOT_LOG_TEST_CREDENTIAL", json.dumps(result))
        self.assertEqual(result["kind"], "review_error")

    def test_compact_state_keeps_decisions_but_hides_large_paper_body(self):
        state.record("literature_call", {"tool": "fixture", "content": ["x" * 30000]}, "fixture")
        service.record_decision("Continue with fixed evidence", "fixture")
        compact = service.get_project_state("fixture")
        self.assertEqual(compact["events"][0]["text"], "Continue with fixed evidence")
        self.assertNotIn("content", compact["events"][1])
        full = service.get_project_state("fixture", include_details=True)
        self.assertIn("content", full["events"][1])

    def test_failed_compile_cannot_return_a_stale_pdf(self):
        source = Path(self.temp.name) / "paper.tex"
        source.write_text("invalid TeX fixture")
        prior = source.with_suffix(".pdf")
        prior.write_bytes(b"%PDF-old-valid-artifact")
        process = types.SimpleNamespace(returncode=1, stdout=b"intentional compile failure", stderr=b"")
        with patch.object(service.shutil, "which", return_value="fixture-compiler"), patch.object(service.subprocess, "run", return_value=process):
            result = service.compile_paper(str(source))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["pdf"])
        self.assertEqual(prior.read_bytes(), b"%PDF-old-valid-artifact")
        self.assertTrue(Path(result["log"]).exists())

    def test_compile_timeout_preserves_diagnostics(self):
        source = Path(self.temp.name) / "paper.tex"
        source.write_text("fixture")
        timeout = subprocess.TimeoutExpired("fixture", 90, output=b"partial compiler output")
        with patch.object(service.shutil, "which", return_value="fixture-compiler"), patch.object(service.subprocess, "run", side_effect=timeout):
            result = service.compile_paper(str(source))
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["pdf"])
        self.assertEqual(Path(result["log"]).read_bytes(), b"partial compiler output")


if __name__ == "__main__":
    unittest.main()
