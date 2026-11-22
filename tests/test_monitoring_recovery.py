import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.harness import monitoring, recovery, service, state, workspace


class MonitoringRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def job(self, name, collected=False, project="sample"):
        state.record("job_submitted", {"job_id": name, "state": "submission_reserved"}, project)
        if collected:
            state.record("job_collected", {"job_id": name, "status": {"state": "completed"}}, project)

    def test_old_active_job_is_recovered_after_130_new_collections(self):
        self.job("old-running")
        state.record("job_status", {"job_id": "old-running", "state": "running"}, "sample")
        for i in range(130):
            self.job(str(i), collected=True)
        with patch.object(service, "get_job_status", return_value={"state": "running"}) as poll, \
             patch.object(service, "collect_job_results") as collect, \
             patch.object(service, "submit_experiment") as submit:
            result = recovery.resume_project("sample")
        poll.assert_called_once_with("old-running")
        collect.assert_not_called()
        submit.assert_not_called()
        self.assertEqual(result["pending_before"], 1)
        self.assertIsNone(result["next_offset"])

    def test_settled_jobs_require_no_remote_calls(self):
        self.job("settled", collected=True)
        with patch.object(service, "get_job_status") as poll, patch.object(service, "collect_job_results") as collect:
            result = recovery.resume_project("sample")
        poll.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(result["jobs"], [])

    def test_pending_job_pagination_is_explicit_and_project_scoped(self):
        for i in range(131):
            self.job(str(i))
        self.job("other", project="elsewhere")
        first = recovery.list_jobs("sample", 100, pending_only=True)
        second = recovery.list_jobs("sample", 100, first["next_offset"], pending_only=True)
        ids = [job["job_id"] for job in first["jobs"] + second["jobs"]]
        self.assertEqual(len(ids), 131)
        self.assertEqual(len(set(ids)), 131)
        self.assertNotIn("other", ids)
        self.assertIsNone(second["next_offset"])
        with self.assertRaises(ValueError):
            recovery.list_jobs("sample", offset=-1)

    def test_dispatch_state_is_visible_before_first_poll(self):
        self.job("queued")
        state.record("job_dispatch", {"job_id": "queued", "state": "waiting_resources"}, "sample")
        self.assertEqual(recovery.list_jobs("sample")["jobs"][0]["state"], "waiting_resources")

    def test_compact_events_hide_review_prompts_and_preserve_full_records(self):
        prompt = "x" * 100000
        state.record("review_input", {"topic": prompt, "request_id": "receipt"}, "sample")
        service.record_decision("Continue using measured evidence", "sample")
        compact = service.get_project_state("sample")
        full = service.get_project_state("sample", include_details=True)
        self.assertLess(len(json.dumps(compact)), 3000)
        self.assertEqual(compact["events"][0]["text"], "Continue using measured evidence")
        self.assertIn("topic", compact["events"][1]["omitted_fields"])
        self.assertEqual(full["events"][1]["topic"], prompt)

    def test_log_incremental_pages_do_not_skip_backlog_or_repeat_entries(self):
        initial = service.record_decision("Start", "sample")
        first = monitoring.process_log("sample", 1)
        self.assertEqual(first["next_event_id"], initial["event_id"])
        for i in range(5):
            service.record_decision(f"Step {i}", "sample")
            state.record("workflow_artifact", {"content": "raw body excluded"}, "sample")
        cursor, seen = first["next_event_id"], []
        for _ in range(3):
            page = monitoring.process_log("sample", 2, cursor)
            seen.extend(entry["message"] for entry in page["entries"])
            cursor = page["next_event_id"]
        self.assertEqual(seen, [f"Step {i}" for i in range(5)])
        empty = monitoring.process_log("sample", 2, cursor)
        self.assertEqual(empty["entries"], [])
        self.assertEqual(empty["next_event_id"], cursor)
        other = service.record_decision("Other project", "other")
        with self.assertRaises(ValueError):
            monitoring.process_log("sample", after_event_id=other["event_id"])

    def test_log_uses_client_timezone_and_bounded_single_lines(self):
        with patch.object(state, "now", return_value="2026-11-18T02:00:00+00:00"):
            service.record_decision("hello\nworld " + "x" * 2000, "sample")
        log = monitoring.process_log("sample")
        self.assertEqual(log["entries"][0]["time"], "2026-11-18T10:00:00+08:00")
        self.assertNotIn("\n", log["lines"][0])
        self.assertLess(len(log["lines"][0]), 300)

    def test_log_identifies_experiment_and_preserves_recorded_next_step(self):
        state.record("job_submitted", {"job_id": "experiment", "state": "submission_reserved",
            "parameters": {"method": "baseline", "dataset": "MVTec", "seed": 42}}, "sample")
        state.record("review_error", {"error_type": "TimeoutError", "next_action": "Inspect receipt before retry"}, "sample")
        log = monitoring.process_log("sample")
        self.assertIn("baseline MVTec seed=42", log["lines"][0])
        self.assertEqual(log["entries"][1]["next_action"], "Inspect receipt before retry")

    def test_overview_stays_small_without_reading_artifact_files_or_writing_events(self):
        workspace.start_project("sample", ["reference"])
        with workspace._db() as db:
            for i in range(500):
                artifact = {"path": f"missing-{i}.json", "sha256": "0" * 64, "kind": "results"}
                db.execute("INSERT INTO workflow_artifacts VALUES (?,?,?)", (str(i), "sample", json.dumps(artifact)))
            before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Must not read artifacts")), \
             patch.object(service, "_remote", side_effect=AssertionError("Must not poll remote")):
            overview = service.project_overview("sample")
        full = workspace.project_status("sample")
        self.assertEqual(overview["artifact_count"], 500)
        self.assertLess(len(json.dumps(overview)), len(json.dumps(full)) / 10)
        self.assertIn("Recorded state only", overview["validation"])
        with state.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
