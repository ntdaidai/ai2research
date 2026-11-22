import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.harness import workspace as w


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.project = w.start_project("sample", ["https://arxiv.org/abs/1706.03762", "paper two"])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def output(self, path="output.md", value="real output"):
        target = w.project_root("sample") / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        return w.register_artifact("sample", path, "report")

    def test_pause_persists_blocks_claims_and_resume_preserves_tasks(self):
        before = w.project_status('sample')
        w.set_project_execution_state('sample', 'paused', 'User requests pause', 'save current branch')
        paused = w.project_status('sample')
        self.assertEqual(paused['execution_state'], 'paused')
        self.assertEqual(paused['ready_task_ids'], [])
        self.assertEqual(len(paused['tasks']), len(before['tasks']))
        with self.assertRaisesRegex(ValueError, 'paused'):
            w.claim_task('sample', before['ready_task_ids'][0], 'worker')
        from tools.harness.controller import research_workflow
        self.assertEqual(research_workflow('next', 'sample')['next_action'], 'paused_by_user')
        self.output('archive-note.md', 'Evidence remains writable for archiving')
        program = "from tools.harness.workspace import project_status; print(project_status('sample')['execution_state'])"
        result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'paused')
        w.set_project_execution_state('sample', 'active', 'User requests resume')
        resumed = w.project_status('sample')
        self.assertEqual(resumed['ready_task_ids'], before['ready_task_ids'])
        self.assertEqual([t['attempts'] for t in resumed['tasks']], [t['attempts'] for t in before['tasks']])

    def complete(self, task):
        lease = w.claim_task("sample", task["task_id"], "worker")
        ids = [self.output(path)["artifact_id"] for path in task["output_paths"] or ["output.md"]]
        return w.update_task("sample", task["task_id"], lease["lease_token"], "completed", artifact_ids=ids)

    def test_start_idempotent_and_cross_process_recovery(self):
        again = w.start_project("sample", self.project["seed_papers"])
        self.assertEqual([t["task_id"] for t in again["tasks"]], [t["task_id"] for t in self.project["tasks"]])
        with self.assertRaises(ValueError):
            w.start_project("sample", ["different input"])
        program = "from tools.harness.workspace import project_status; import json; print(json.dumps(project_status('sample')))"
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["tasks"][0]["task_id"], again["tasks"][0]["task_id"])
        self.assertTrue((w.project_root("sample") / again["tasks"][0]["prompt_path"]).is_file())

    def test_concurrent_claim_has_single_winner(self):
        task_id = self.project["tasks"][0]["task_id"]
        def claim(index):
            try:
                return w.claim_task("sample", task_id, str(index))
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_dependencies_outputs_and_stale_tokens(self):
        predecessor = self.project["tasks"][0]
        successor = w.create_task("sample", "analysis", "analysis", [predecessor["task_id"]], output_paths=["analysis.md"])
        with self.assertRaises(ValueError):
            w.claim_task("sample", successor["task_id"], "analyst")
        self.complete(predecessor)
        lease = w.claim_task("sample", successor["task_id"], "analyst", lease_seconds=1)
        with self.assertRaises(ValueError):
            w.update_task("sample", successor["task_id"], lease["lease_token"], "completed")
        with patch.object(w.time, "time", return_value=time.time() + 2):
            replacement = w.claim_task("sample", successor["task_id"], "other")
        with self.assertRaises(ValueError):
            w.update_task("sample", successor["task_id"], lease["lease_token"], "failed")
        artifact = self.output("analysis.md")
        w.update_task("sample", successor["task_id"], replacement["lease_token"], "completed", artifact_ids=[artifact["artifact_id"]])
        with self.assertRaises(ValueError):
            w.claim_task("sample", successor["task_id"], "again")

    def test_tampering_and_cross_project_artifact_rejected(self):
        task = self.project["tasks"][0]
        lease = w.claim_task("sample", task["task_id"], "worker")
        path = task["output_paths"][0]
        artifact = self.output(path)
        (w.project_root("sample") / path).write_text("changed later")
        with self.assertRaises(ValueError):
            w.update_task("sample", task["task_id"], lease["lease_token"], "completed", artifact_ids=[artifact["artifact_id"]])
        w.start_project("other", ["paper"])
        (w.project_root("other") / "x.md").write_text("other project")
        other = w.register_artifact("other", "x.md", "report")
        with self.assertRaises(ValueError):
            w.update_task("sample", task["task_id"], lease["lease_token"], "completed", artifact_ids=[other["artifact_id"]])

    def test_paths_and_task_keys(self):
        for invalid in ("../escape", ".", "/tmp/escape", "a/b"):
            with self.assertRaises(ValueError):
                w.project_root(invalid)
        for invalid in ("../escape", "/etc/passwd", "x\\y", "."):
            with self.assertRaises(ValueError):
                w.register_artifact("sample", invalid, "report")
        root = w.project_root("sample")
        (root / "linked").symlink_to(Path(self.tmp.name))
        with self.assertRaises(ValueError):
            w.create_task("sample", "bad", "test", output_paths=["linked/file"])
        task = w.create_task("sample", "unique", "test", task_key="stable")
        self.assertEqual(w.create_task("sample", "unique", "test", task_key="stable")["task_id"], task["task_id"])
        with self.assertRaises(ValueError):
            w.create_task("sample", "changed", "test", task_key="stable")
        with self.assertRaises(ValueError):
            w.create_task("sample", "bad dep", "test", dependencies=["unknown"])

    def test_dependency_artifact_changed_blocks_claim(self):
        task = self.project["tasks"][0]
        self.complete(task)
        successor = w.create_task("sample", "next", "test", [task["task_id"]])
        (w.project_root("sample") / task["output_paths"][0]).write_text("changed after completion")
        with self.assertRaises(ValueError):
            w.claim_task("sample", successor["task_id"], "new worker")
        board = w.project_status("sample")
        updated = {task["task_id"]: task for task in board["tasks"]}
        self.assertFalse(updated[task["task_id"]]["artifacts_valid"])
        self.assertFalse(updated[successor["task_id"]]["ready"])

    def test_running_update_renews_current_lease(self):
        task = self.project["tasks"][0]
        lease = w.claim_task("sample", task["task_id"], "worker", lease_seconds=100)
        with patch.object(w.time, "time", return_value=time.time() + 20):
            renewed = w.update_task("sample", task["task_id"], lease["lease_token"], "running", summary="Still working")
        self.assertGreater(renewed["lease_expires_unix"], lease["lease_expires_unix"] + 10)

    def test_reverted_snapshot_becomes_latest_registered_revision(self):
        first = self.output("revised.md", "revision A")
        second = self.output("revised.md", "revision B")
        reverted = self.output("revised.md", "revision A")
        self.assertEqual(first["artifact_id"], reverted["artifact_id"])
        revisions = [a for a in w.list_artifacts("sample") if a["path"] == "revised.md"]
        self.assertEqual([a["artifact_id"] for a in revisions], [second["artifact_id"], first["artifact_id"]])

    def test_toolchain_evidence_and_tokens_are_not_in_public_records(self):
        w.start_project("toolchain", ["test paper"], evidence_kind="toolchain_test")
        (w.project_root("toolchain") / "output.md").write_text("test output")
        artifact = w.register_artifact("toolchain", "output.md", "report")
        self.assertEqual(artifact["evidence_kind"], "toolchain_test")
        task = w.create_task("sample", "keyed", "test", task_key="keyed")
        lease = w.claim_task("sample", task["task_id"], "worker")
        again = w.create_task("sample", "keyed", "test", task_key="keyed")
        self.assertNotIn("lease_token", again)
        from tools.harness.state import events
        self.assertNotIn(lease["lease_token"], json.dumps(events("sample")))


if __name__ == "__main__":
    unittest.main()
