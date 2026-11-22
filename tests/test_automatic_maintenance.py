import asyncio
import inspect
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.harness import maintenance, service, state, workspace


class AutomaticMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def checkpoint(self, project="sample"):
        status = service.maintenance_status(project)
        return status, json.loads(Path(status["checkpoint"]).read_text())

    def due(self, project="sample"):
        with state.connect() as db:
            db.execute("UPDATE project_maintenance SET updated_unix=0,lease_until=0 WHERE project=?", (project,))

    def test_normal_operations_create_and_refresh_handoff_without_manual_maintenance(self):
        service.start_project("sample", ["reference"], evidence_kind="toolchain_test")
        service.record_decision("Next: compare the preserved controls", "sample")
        status, saved = self.checkpoint()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(saved["latest_decision"], "Next: compare the preserved controls")
        folder = Path(status["checkpoint"]).parent
        self.assertIn(saved["generation"], (folder / "handoff.md").read_text())
        self.assertIn(saved["generation"], (folder / "progress.log").read_text())
        with patch.object(maintenance, "_snapshot", side_effect=AssertionError("Unchanged read must not regenerate")):
            service.get_project_state("sample")

    def test_due_query_cleans_registered_crash_leftovers_without_new_events(self):
        service.record_decision("Preserve evidence", "sample")
        root = Path(self.tmp.name)
        stage = root / ".collections" / "collection-abcdefgh"
        stage.mkdir(parents=True)
        (stage / "partial.bin").write_bytes(b"partial")
        owner = stage.with_name(stage.name + ".owner.json")
        owner.write_text(json.dumps({"pid": 99999999, "hostname": socket.gethostname(),
                                     "created_unix": time.time() - 90000}))
        evidence = root / "jobs" / "existing" / "result.bin"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"keep")
        self.due()
        service.get_project_state("sample")
        self.assertFalse(stage.exists())
        self.assertFalse(owner.exists())
        self.assertEqual(evidence.read_bytes(), b"keep")
        self.assertEqual(self.checkpoint()[1]["cleanup"]["removed_stages"], 1)

    def test_failed_maintenance_preserves_success_watermark_and_retries_with_backoff(self):
        service.record_decision("Initial decision", "sample")
        old = self.checkpoint()[1]
        stderr = io.StringIO()
        with patch.object(maintenance, "_snapshot", side_effect=OSError("DO_NOT_LOG_SENSITIVE_DETAILS")) as build, \
             patch.object(maintenance.sys, "stderr", stderr):
            result = service.record_decision("New committed decision", "sample")
            service.get_project_state("sample")
            self.assertEqual(build.call_count, 1)
        self.assertEqual(result["text"], "New committed decision")
        failed, saved = self.checkpoint()
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(saved["event_watermark"], old["event_watermark"])
        self.assertNotIn("DO_NOT_LOG_SENSITIVE_DETAILS", stderr.getvalue())
        self.due()
        service.get_project_state("sample")
        self.assertEqual(self.checkpoint()[1]["latest_decision"], "New committed decision")

    def test_expired_lease_is_reported_without_marking_work_complete(self):
        project = service.start_project("sample", ["reference"])
        task_id = project["ready_task_ids"][0]
        workspace.claim_task("sample", task_id, "worker")
        with workspace._db() as db:
            task = workspace._task(db, "sample", task_id)
            task["lease_expires_unix"] = time.time() - 1
            workspace._save_task(db, task)
        self.due()
        service.get_project_state("sample")
        saved = self.checkpoint()[1]
        self.assertIn("expired_task_leases", [issue["code"] for issue in saved["issues"]])
        self.assertEqual(workspace.project_status("sample")["tasks"][0]["state"], "running")

    def test_partial_snapshot_write_keeps_previous_commit_and_recovers_automatically(self):
        service.record_decision("Previous checkpoint", "sample")
        previous = self.checkpoint()[1]
        write = maintenance._atomic_write
        def fail_commit(path, data):
            if path.name == "checkpoint.json":
                raise OSError("simulated checkpoint write failure")
            return write(path, data)
        with patch.object(maintenance, "_atomic_write", side_effect=fail_commit), \
             patch.object(maintenance.sys, "stderr", io.StringIO()):
            service.record_decision("New decision remains committed in state", "sample")
        failed, saved = self.checkpoint()
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(saved["generation"], previous["generation"])
        self.assertEqual(failed["event_watermark"], previous["event_watermark"])
        self.due()
        service.get_project_state("sample")
        status, recovered = self.checkpoint()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(recovered["latest_decision"], "New decision remains committed in state")

    def test_dispatch_defaults_compact_but_preserves_real_scheduling_checks(self):
        service.start_project("sample", ["reference"])
        with workspace._db() as db:
            for i in range(500):
                db.execute("INSERT INTO workflow_artifacts VALUES (?,?,?)", (str(i), "sample", json.dumps({"path": f"missing-{i}", "sha256": "0" * 64})))
        compact = service.research_workflow("next", "sample")
        full = service.research_workflow("next", "sample", include_details=True)
        self.assertEqual(compact["next_action"], full["next_action"])
        self.assertEqual(compact["ready_task_ids"], full["ready_task_ids"])
        self.assertEqual(compact["artifact_count"], 500)
        self.assertNotIn("artifacts", compact)
        self.assertTrue(compact["ready_tasks"][0]["prompt_path"])
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) / 5)

    def test_comparison_jobs_resolve_project_for_automatic_maintenance(self):
        for name in ("baseline", "candidate"):
            state.record("job_submitted", {"job_id": name}, "sample")
        signature = inspect.signature(service.compare_jobs)
        bound = signature.bind("baseline", "candidate", "score")
        bound.apply_defaults()
        with patch.object(maintenance, "maintain_project", return_value={"state": "updated"}) as update:
            maintenance._after_call("compare_jobs", bound, "")
        update.assert_called_once()
        self.assertEqual(update.call_args.args[0], "sample")

    def test_compact_selection_preserves_named_task_mapping(self):
        board = service.start_project("sample", ["synthetic reference"], evidence_kind="toolchain_test")
        task = board["tasks"][0]
        lease = workspace.claim_task("sample", task["task_id"], "fixture")
        artifacts = []
        for relative in task["output_paths"]:
            path = workspace.project_root("sample") / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic screening handoff; no research claim")
            artifacts.append(workspace.register_artifact("sample", relative, "candidate_card")["artifact_id"])
        workspace.update_task("sample", task["task_id"], lease["lease_token"], "completed", artifact_ids=artifacts)
        selected = service.research_workflow("select", "sample", task_id=task["task_id"], reason="Infrastructure fixture")
        self.assertIsInstance(selected["tasks"], dict)
        self.assertIn("method", selected["tasks"])
        self.assertTrue(selected["tasks"]["method"]["prompt_path"])
        # A changed completed handoff must be repaired before workflow advances.
        changed = workspace.project_root("sample") / task["output_paths"][0]
        changed.write_text("Changed after completion")
        next_step = service.research_workflow("next", "sample")
        self.assertEqual(next_step["next_action"], "repair_changed_task_evidence")
        self.assertIn(task["task_id"], next_step["invalid_task_ids"])

    def test_diagnostic_failure_never_overrides_tool_success_or_original_error(self):
        def operation(project_id):
            return "successful operation"
        def broken(project_id):
            raise ValueError("original error")
        for fn in (operation, broken):
            fn.__module__ = "fake_service"
        namespace = {"__name__": "fake_service", "operation": operation, "broken": broken}
        maintenance.instrument_service(namespace)
        with patch.object(maintenance, "_after_call", side_effect=RuntimeError("maintenance failed")), \
             patch.object(maintenance.sys, "stderr") as stderr:
            stderr.write.side_effect = OSError("closed stderr")
            self.assertEqual(namespace["operation"]("sample"), "successful operation")
            with self.assertRaisesRegex(ValueError, "original error"):
                namespace["broken"]("sample")

    def test_async_signature_and_nested_calls_maintain_once_after_completion(self):
        trace = []
        namespace = {"__name__": "fake_service"}
        def inner(project_id):
            trace.append("inner")
        async def outer(project_id):
            namespace["inner"](project_id)
            await asyncio.sleep(0)
            trace.append("outer completed")
            return 42
        for fn in (inner, outer):
            fn.__module__ = "fake_service"
            namespace[fn.__name__] = fn
        maintenance.instrument_service(namespace)
        self.assertEqual(inspect.signature(namespace["outer"]), inspect.signature(outer))
        with patch.object(maintenance, "_after_call", side_effect=lambda *args: trace.append("maintenance")) as update:
            self.assertEqual(asyncio.run(namespace["outer"]("sample")), 42)
        self.assertEqual(trace, ["inner", "outer completed", "maintenance"])
        update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
