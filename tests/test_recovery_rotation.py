import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tools.harness import recovery, service, state


class RecoveryRotationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.temporary.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def jobs(self, count, project="sample"):
        for index in range(count):
            state.record("job_submitted", {"job_id": f"job-{index}", "state": "running"}, project)

    def test_running_first_page_cannot_starve_old_jobs(self):
        self.jobs(65)
        with patch.object(service, "get_job_status", return_value={"state": "running"}) as poll:
            for _ in range(3):
                recovery.resume_project("sample", limit=30)
        first_sweep = [call.args[0] for call in poll.call_args_list[:65]]
        self.assertEqual(len(set(first_sweep)), 65)
        self.assertIn("job-0", first_sweep)

    def test_collection_and_new_submission_do_not_shift_cursor(self):
        self.jobs(6)

        def collected(job_id):
            state.record("job_collected", {"job_id": job_id, "status": {"state": "completed"}}, "sample")
            return {"local_path": "fixture", "mlflow_run_id": job_id}

        with patch.object(service, "get_job_status", return_value={"state": "completed"}) as poll, \
             patch.object(service, "collect_job_results", side_effect=collected):
            recovery.resume_project("sample", limit=2)
            state.record("job_submitted", {"job_id": "new", "state": "running"}, "sample")
            recovery.resume_project("sample", limit=2)
            recovery.resume_project("sample", limit=2)
            recovery.resume_project("sample", limit=2)
        ids = [call.args[0] for call in poll.call_args_list]
        self.assertEqual(ids, ["job-5", "job-4", "job-3", "job-2", "job-1", "job-0", "new"])

    def test_cursor_is_persistent_and_project_scoped(self):
        self.jobs(4)
        self.jobs(4, "elsewhere")
        with patch.object(service, "get_job_status", return_value={"state": "running"}) as poll:
            recovery.resume_project("sample", limit=2)
            recovery.resume_project("elsewhere", limit=2)
        self.assertEqual([call.args[0] for call in poll.call_args_list],
                         ["job-3", "job-2", "job-3", "job-2"])
        child = subprocess.run([sys.executable, "-B", "-c", """
import json
from tools.harness import recovery, service
service.get_job_status = lambda job_id: {'state': 'running'}
print(json.dumps(recovery.resume_project('sample', limit=2)))
"""], cwd=state.ROOT, check=True, capture_output=True, text=True)
        self.assertEqual([job["job_id"] for job in json.loads(child.stdout)["jobs"]], ["job-1", "job-0"])

    def test_concurrent_reservations_are_disjoint(self):
        self.jobs(6)
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait()
            return recovery._reserve_resume_jobs("sample", 3, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: reserve(), range(2)))
        first_ids = {job["job_id"] for job in first[0]}
        second_ids = {job["job_id"] for job in second[0]}
        self.assertEqual(len(first_ids | second_ids), 6)
        self.assertFalse(first_ids & second_ids)

    def test_crashed_reservations_expire_and_failures_release(self):
        self.jobs(2)
        jobs, token, _ = recovery._reserve_resume_jobs("sample", 2, None)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(recovery._reserve_resume_jobs("sample", 2, None)[0], [])
        with state.connect() as db:
            db.execute("UPDATE recovery_reservations SET expires_unix=0 WHERE token=?", (token,))
        with patch.object(service, "get_job_status", side_effect=RuntimeError("unreachable")):
            result = recovery.resume_project("sample", limit=2)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertTrue(all(job["state"] == "needs_attention" for job in result["jobs"]))
        with state.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recovery_reservations").fetchone()[0], 0)

    def test_reclaimed_batch_member_is_not_polled_by_stale_owner(self):
        self.jobs(2)

        def status(job_id):
            with state.connect() as db:
                db.execute("UPDATE recovery_reservations SET token='new-owner' WHERE job_id='job-0'")
            return {"state": "running"}

        with patch.object(service, "get_job_status", side_effect=status) as poll:
            result = recovery.resume_project("sample", limit=2)
        poll.assert_called_once_with("job-1")
        self.assertEqual(result["jobs"][1]["state"], "needs_attention")
        with state.connect() as db:
            self.assertEqual(db.execute("SELECT token FROM recovery_reservations").fetchone()[0], "new-owner")

    def test_paused_project_can_collect_without_dispatch_or_resume(self):
        self.jobs(2)
        state.record("project_execution_control", {"execution_state": "paused"}, "sample")
        with patch.object(service, "get_job_status", return_value={"state": "running"}), \
             patch.object(service, "submit_experiment", side_effect=AssertionError("Must not submit")):
            result = recovery.resume_project("sample", limit=1)
        self.assertEqual(len(result["jobs"]), 1)
        with state.connect() as db:
            self.assertEqual(state.execution_state(db, "sample")["execution_state"], "paused")

    def test_explicit_offset_remains_manual_and_does_not_move_auto_cursor(self):
        self.jobs(4)
        with patch.object(service, "get_job_status", return_value={"state": "running"}) as poll:
            manual = recovery.resume_project("sample", limit=2, offset=2)
            automatic = recovery.resume_project("sample", limit=2)
        self.assertEqual([call.args[0] for call in poll.call_args_list], ["job-1", "job-0", "job-3", "job-2"])
        self.assertEqual(manual["pagination_mode"], "offset")
        self.assertEqual(automatic["pagination_mode"], "rotation")
        with self.assertRaises(ValueError):
            recovery.resume_project("sample", offset=True)


if __name__ == "__main__":
    unittest.main()
