import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.harness import service
from tools.harness.experiments import approve_proposal
from tools.harness.state import events, record


class ResearchCycleTest(unittest.TestCase):
    def test_recovery_reuses_job_then_keeps_improvement_and_stops_at_target(self):
        with tempfile.TemporaryDirectory() as state_dir, patch.dict(os.environ, {"RESEARCH_STATE_DIR": state_dir}):
            root = Path(state_dir)
            method = root / "method.py"
            (root / "evaluate.py").write_text("# Frozen evaluator", encoding="utf-8")
            project = "synthetic-cycle-test"

            def prepare(source, parent=""):
                method.write_text(source, encoding="utf-8")
                return service.prepare_experiment(
                    project, source, str(method), input_files=["evaluate.py"], evaluation_files=["evaluate.py"],
                    parent_proposal_id=parent, purpose="toolchain_test")["proposal_id"]

            baseline = prepare("print('baseline')")
            winner = prepare("print('winner')", baseline)
            unused = prepare("print('unused')", baseline)
            approval = approve_proposal(baseline, "Unit test approval fixture", max_runs=3, allow_code_iterations=True)

            def cycle(action, **kwargs):
                return asyncio.run(service.research_cycle(action, project_id=project, **kwargs))

            start = dict(approval_id=approval["event_id"], hypothesis="Synthetic comparison", metric="error", target=1)
            cycle_id = cycle("start", **start)["cycle_id"]
            self.assertEqual(cycle("start", **start)["cycle_id"], cycle_id)
            cycle("enqueue", cycle_id=cycle_id, candidate_proposal_ids=[winner, unused])
            cycle("enqueue", cycle_id=cycle_id, candidate_proposal_ids=[winner])
            requests = []

            def remote(request):
                job_id = request["job_id"]
                if request["operation"] == "submit":
                    requests.append(job_id)
                    if len(requests) == 1:
                        raise TimeoutError("deliberately lost first reply")
                    return {"job_id": job_id, "state": "submitted"}
                return {"job_id": job_id, "state": "completed"}

            def collect(job_id):
                trial = {"job_id": job_id, "status": {"state": "completed"},
                         "metrics": {"error": 2 if job_id == requests[0] else 1}, "mlflow_run_id": job_id}
                return record("job_collected", trial, project)

            with patch.object(service, "_remote", side_effect=remote), patch.object(service, "collect_job_results", side_effect=collect):
                with self.assertRaises(TimeoutError):
                    cycle("advance", cycle_id=cycle_id)
                self.assertEqual(cycle("status", cycle_id=cycle_id)["state"], "in_progress")
                for expected in ("dispatched", "evaluated", "dispatched", "evaluated", "target_reached"):
                    outcome = cycle("advance", cycle_id=cycle_id)
                    self.assertEqual(outcome["state"], expected)
            self.assertEqual(requests[0], requests[1])
            self.assertEqual(len(events(project, kind="job_submitted")), 2)
            self.assertEqual(outcome["best"]["proposal_id"], winner)
            self.assertEqual([item["decision"] for item in outcome["decisions"]], ["baseline", "keep"])
