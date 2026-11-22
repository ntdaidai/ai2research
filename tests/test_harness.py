import concurrent.futures
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from harness import service, state


class HarnessGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_state_persists_and_separates_projects(self):
        service.record_decision("Keep distinct experiments", "direction-a")
        service.record_decision("Separate record", "direction-b")
        self.assertEqual(service.get_project_state("direction-a")["events"][0]["text"], "Keep distinct experiments")
        self.assertEqual(len(service.get_project_state("direction-b")["events"]), 1)

    def test_event_metadata_cannot_be_overridden(self):
        result = state.record("actual", {"kind": "wrong", "event_id": "wrong"})
        self.assertEqual(result["kind"], "actual")
        self.assertNotEqual(result["event_id"], "wrong")

    def test_concurrent_review_reservations_do_not_exceed_limit(self):
        # Ensure schema exists before concurrency; transactions serialize reservations.
        state.connect().close()
        def reserve(_):
            try:
                return state.reserve_review("test")
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(x is not None for x in results), state.config()["review"]["max_calls"])

    def test_unknown_or_traversal_jobs_never_call_ssh(self):
        with patch.object(service, "_remote") as remote:
            for job_id in ("../other-user", "a" * 32):
                with self.assertRaises(ValueError):
                    service.cancel_job(job_id)
            remote.assert_not_called()

    def test_unapproved_experiment_never_calls_ssh(self):
        script = Path(self.temp.name) / "script.py"
        script.write_text("print('no launch')")
        service.record_decision("Discussion is not approval", "test")
        with patch.object(service, "_remote") as remote:
            with self.assertRaises(ValueError):
                service.submit_experiment("test", str(script), "nonexistent")
            remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
