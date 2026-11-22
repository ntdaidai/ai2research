"""Dispatch uncertainty keeps the original identity, diagnostic and run budget."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.harness import experiments, service
from tools.harness.state import events


class DispatchFailureTests(unittest.TestCase):
    def test_lost_reply_records_safe_diagnostic_without_marking_job_failed(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            script = Path(folder) / "method.py"
            script.write_text("# isolated dispatch fixture; never executed")
            proposal = experiments.prepare_experiment("dispatch-fixture", "fixture", str(script), purpose="toolchain_test")
            approval = experiments.approve_proposal(proposal["proposal_id"], "Isolated unit fixture; no live execution", 1)
            lost = TimeoutError("DO_NOT_PERSIST_EXCEPTION_TEXT_OR_HEADER")
            with patch.object(service, "_remote", side_effect=lost) as remote:
                with self.assertRaises(TimeoutError) as caught:
                    experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])
                self.assertIs(caught.exception, lost)
                self.assertEqual(remote.call_count, 1)
            submitted = events("dispatch-fixture", kind="job_submitted")
            diagnostics = events("dispatch-fixture", kind="job_dispatch_error")
            self.assertEqual(len(submitted), 1)
            self.assertEqual(len(diagnostics), 1)
            error = diagnostics[0]
            self.assertEqual(error["job_id"], submitted[0]["job_id"])
            self.assertEqual(error["proposal_id"], proposal["proposal_id"])
            self.assertEqual(error["approval_id"], approval["event_id"])
            self.assertEqual(error["state"], "dispatch_uncertain")
            self.assertEqual(error["error_type"], "TimeoutError")
            self.assertIn("retry_job_dispatch", error["next_action"])
            self.assertNotIn("DO_NOT_PERSIST", json.dumps(events("dispatch-fixture")))

    def test_same_cycle_recovers_original_id_and_failed_dispatch_consumes_budget(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            script = Path(folder) / "method.py"
            script.write_text("# isolated recovery fixture; never executed")
            proposal = experiments.prepare_experiment("recovery-fixture", "fixture", str(script), purpose="toolchain_test")
            approval = experiments.approve_proposal(proposal["proposal_id"], "Isolated unit fixture; no live execution", 1)
            cycle_id = "bounded-dispatch-fixture"
            with patch.object(service, "_remote", side_effect=TimeoutError("lost reply")):
                with self.assertRaises(TimeoutError):
                    experiments.submit_proposal(proposal["proposal_id"], approval["event_id"], cycle_id=cycle_id)
            job_id = events("recovery-fixture", kind="job_submitted")[0]["job_id"]
            with patch.object(service, "_remote", return_value={"job_id": job_id, "state": "already_submitted"}) as remote:
                result = experiments.submit_proposal(proposal["proposal_id"], approval["event_id"], cycle_id=cycle_id)
                self.assertEqual(result["job_id"], job_id)
                self.assertEqual(remote.call_args.args[0]["job_id"], job_id)
                self.assertEqual(remote.call_count, 1)
                with self.assertRaisesRegex(ValueError, "exhausted"):
                    experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])
                self.assertEqual(remote.call_count, 1)
            self.assertEqual(len(events("recovery-fixture", kind="job_submitted")), 1)
            self.assertEqual(len(events("recovery-fixture", kind="job_dispatch_error")), 1)


if __name__ == "__main__":
    unittest.main()
