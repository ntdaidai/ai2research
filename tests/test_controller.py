"""Behavioral checks for the live-controller hand-offs; no remote/model calls."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from tools.harness import controller, service, workspace
from tools.harness.experiments import prepare_experiment
from tools.harness.state import ROOT, events, record


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ai2-controller-test-")
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.project_id = "controller-test"
        self.board = workspace.start_project(self.project_id, ["paper-a", "paper-b"], evidence_kind="toolchain_test")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def cli(self, tool, arguments):
        result = subprocess.run([str(ROOT / "ai2research"), tool, arguments],
                                cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def finish_screening(self, task):
        lease = workspace.claim_task(self.project_id, task["task_id"], "screening-test-agent")
        path = task["output_paths"][0]
        target = workspace.project_root(self.project_id) / path
        target.write_text("Toolchain fixture: paper screening is not a scientific claim.\n")
        artifact = workspace.register_artifact(self.project_id, path, "candidate_card")
        return workspace.update_task(self.project_id, task["task_id"], lease["lease_token"],
                                     "completed", artifact_ids=[artifact["artifact_id"]])

    def collected_fixture(self, *, project_id=None, job_id=None, independent=True,
                          synthetic=True, proposal_project=None):
        project_id = project_id or self.project_id
        job_id = job_id or uuid.uuid4().hex
        folder = Path(self.tmp.name) / "jobs" / job_id
        folder.mkdir(parents=True)
        spec = {"kind": "synthetic_workflow_test" if synthetic else "approved_gpu_experiment",
                "evaluator": "evaluate.py" if independent else "", "script_sha256": hashlib.sha256(b"print('fixture')\n").hexdigest()}
        (folder / "script.py").write_bytes(b"print('fixture')\n")
        (folder / "evaluate.py").write_bytes(b"# Fixed evaluator fixture for the import contract.\n")
        if not synthetic or proposal_project:
            proposal = prepare_experiment(proposal_project or project_id, "Import contract fixture", str(folder / "script.py"),
                                          input_files=["evaluate.py"], evaluation_files=["evaluate.py"] if independent else [],
                                          purpose="research")
            spec.update(proposal_id=proposal["proposal_id"], manifest_sha256=proposal["manifest_sha256"])
            # This isolated submission is fixture evidence only; no worker or
            # user-approval record is created by this unit test.
            record("job_submitted", {"job_id": job_id, "proposal_id": proposal["proposal_id"], "spec": spec}, project_id)
        status = {"job_id": job_id, "state": "completed", "job_kind": spec["kind"],
                  "metrics_authority": "evaluate.py" if independent else "script.py"}
        contents = {
            "spec.json": json.dumps(spec).encode(),
            "script.py": b"print('fixture')\n",
            "config.json": b'{"purpose":"toolchain_test"}',
            "status.json": json.dumps(status).encode(),
            "evaluate.py": b"# Fixed evaluator fixture for the import contract.\n",
            "metrics.json": b'{"metrics":{"fixture_metric":1.0}}',
        }
        files = []
        for name, raw in contents.items():
            (folder / name).write_bytes(raw)
            files.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
        record("job_collected", {"job_id": job_id, "status": status, "metrics_error": None,
                                 "metrics": {"fixture_metric": 1.0}, "local_path": str(folder),
                                 "files": files, "mlflow_run_id": "toolchain-fixture"}, project_id)
        return job_id, folder, contents

    def test_cli_at_json_start_is_idempotent_and_next_recovers(self):
        arguments = Path(self.tmp.name) / "start.json"
        arguments.write_text(json.dumps({"project_id": "cli-start", "seed_papers": ["paper-1", "paper-2"],
                                         "evidence_kind": "toolchain_test"}), encoding="utf-8-sig")
        first = self.cli("start_project", "@" + str(arguments))
        second = self.cli("start_project", "@" + str(arguments))
        self.assertEqual([t["task_id"] for t in first["tasks"]], [t["task_id"] for t in second["tasks"]])
        following = self.cli("research_workflow", json.dumps({"action": "next", "project_id": "cli-start"}))
        self.assertEqual(following["next_action"], "delegate_screening")
        self.assertEqual(len(following["ready_tasks"]), 2)
        self.assertEqual(following["evidence_kind"], "toolchain_test")

    def test_changed_screening_cannot_record_a_selection(self):
        completed = self.finish_screening(self.board["tasks"][0])
        (workspace.project_root(self.project_id) / completed["output_paths"][0]).write_text("Changed after task completion")
        with self.assertRaises(ValueError):
            controller.select_candidate(self.project_id, completed["task_id"], "Use this evidence")
        self.assertEqual(events(self.project_id, kind="workflow_selection"), [])
        self.assertEqual(len(workspace.project_status(self.project_id)["tasks"]), 2)

    def test_selection_creates_parallel_dag_without_executing(self):
        completed = self.finish_screening(self.board["tasks"][0])
        with patch.object(service, "_remote", side_effect=AssertionError("Planning must not start workers")) as remote:
            selected = controller.select_candidate(self.project_id, completed["task_id"], "Toolchain selection fixture")
            repeated = controller.select_candidate(self.project_id, completed["task_id"], "Toolchain selection fixture")
            next_step = controller.research_workflow("next", self.project_id)
        remote.assert_not_called()
        self.assertEqual(set(selected["tasks"]), {"method", "baseline", "evaluator", "search", "verification", "ablation"})
        self.assertEqual({k: v["task_id"] for k, v in selected["tasks"].items()},
                         {k: v["task_id"] for k, v in repeated["tasks"].items()})
        tasks = selected["tasks"]
        expected_ready = {tasks[key]["task_id"] for key in ("method", "baseline", "evaluator")}
        # The unselected second paper remains independently available to its screener.
        self.assertTrue(expected_ready.issubset({t["task_id"] for t in next_step["ready_tasks"]}))
        self.assertEqual(set(tasks["search"]["dependencies"]), expected_ready)
        self.assertEqual(tasks["verification"]["dependencies"], [tasks["search"]["task_id"]])
        self.assertEqual(tasks["ablation"]["dependencies"], [tasks["search"]["task_id"]])
        self.assertEqual(selected["selection"]["artifact_ids"], completed["artifact_ids"])
        self.assertTrue(selected["selection"]["artifact_sha256"])
        self.assertEqual(len(events(self.project_id, kind="workflow_selection")), 1)
        self.assertEqual(events(self.project_id, kind="experiment_approval"), [])
        self.assertEqual(events(self.project_id, kind="job_submitted"), [])

    def test_competing_candidate_selections_have_one_consistent_winner(self):
        # Synchronize only at the public call boundary, then let real SQLite
        # transactions and task creation compete. No history result is mocked.
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                self.project_id = f"selection-race-{attempt}"
                board = workspace.start_project(self.project_id, ["paper-a", "paper-b"], evidence_kind="toolchain_test")
                completed = [self.finish_screening(task) for task in board["tasks"]]
                barrier = threading.Barrier(2)

                def select(task):
                    barrier.wait(timeout=10)
                    try:
                        return {"result": controller.select_candidate(self.project_id, task["task_id"],
                                                                      "Evidence supports " + task["task_id"])}
                    except ValueError as exc:
                        return {"rejected": task["task_id"], "reason": str(exc)}

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    outcomes = list(pool.map(select, completed))
                successful = [item["result"] for item in outcomes if "result" in item]
                self.assertEqual(len(successful), 1)
                self.assertEqual(sum("rejected" in item for item in outcomes), 1)
                history = events(self.project_id, kind="workflow_selection")
                self.assertEqual(len(history), 1)
                winner = successful[0]["selection"]["task_id"]
                self.assertEqual(history[0]["task_id"], winner)
                loser = next(item["rejected"] for item in outcomes if "rejected" in item)
                self.assertNotEqual(winner, loser)
                tasks = workspace.project_status(self.project_id)["tasks"]
                selected = [task for task in tasks if task["task_key"].startswith("selected-")]
                self.assertEqual(len(selected), 6)
                for task in selected:
                    self.assertNotIn(loser, task["dependencies"])
                    if task["role"] in ("method_designer", "implementation", "evaluation"):
                        self.assertEqual(task["dependencies"], [winner])
                self.assertEqual(history[0]["artifact_ids"], successful[0]["selection"]["artifact_ids"])

    def test_attach_requires_a_collected_job_in_the_same_project(self):
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, uuid.uuid4().hex)
        workspace.start_project("foreign", ["foreign paper"], evidence_kind="toolchain_test")
        foreign_job, _, _ = self.collected_fixture(project_id="foreign")
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, foreign_job)
        scoped_job, _, _ = self.collected_fixture(proposal_project="foreign")
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, scoped_job)
        self.assertEqual(workspace.list_artifacts(self.project_id), [])

    def test_attach_rejects_method_self_evaluation(self):
        job_id, _, _ = self.collected_fixture(independent=False, synthetic=False)
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, job_id)
        mismatch_id, _, _ = self.collected_fixture(independent=True, synthetic=False)
        saved = next(row for row in events(self.project_id, kind="job_collected") if row["job_id"] == mismatch_id)
        saved["status"]["metrics_authority"] = "script.py"
        record("job_collected", saved, self.project_id)
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, mismatch_id)
        self.assertEqual(workspace.list_artifacts(self.project_id), [])

    def test_attach_checks_all_sources_before_publishing_any(self):
        job_id, folder, _ = self.collected_fixture()
        # Late corruption must not leave earlier files registered as valid evidence.
        (folder / "metrics.json").write_text('{"metrics":{"fixture_metric":999}}')
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, job_id)
        self.assertEqual(workspace.list_artifacts(self.project_id), [])
        destination = workspace.project_root(self.project_id) / "results" / "search" / job_id
        self.assertFalse(destination.exists() and any(destination.rglob("*")))

    def test_attachment_publication_failure_leaves_no_partial_files_and_retries(self):
        job_id, _, contents = self.collected_fixture()
        root = workspace.project_root(self.project_id)
        destination = root / "results" / "search" / job_id
        inspected = []

        def fail_publish(source, target, *args, **kwargs):
            # The actual snapshot must already be completely written when the
            # filesystem publication fails; the final name remains absent.
            self.assertEqual(Path(source).read_bytes(), contents[Path(target).name])
            self.assertFalse(Path(target).exists())
            inspected.append(Path(target))
            raise OSError("Injected filesystem publication failure")

        with patch.object(controller.os, "link", side_effect=fail_publish):
            with self.assertRaisesRegex(OSError, "Injected filesystem"):
                controller.attach_job_evidence(self.project_id, job_id)
        self.assertTrue(inspected)
        self.assertFalse(any((destination / name).exists() for name in contents))
        self.assertEqual(list(root.rglob(".attachment-*")), [])
        self.assertEqual(workspace.list_artifacts(self.project_id), [])
        self.assertEqual(events(self.project_id, kind="job_evidence_attached"), [])

        resumed = controller.attach_job_evidence(self.project_id, job_id)
        self.assertTrue(resumed["artifacts"])
        for name, raw in contents.items():
            self.assertEqual((destination / name).read_bytes(), raw)
        self.assertEqual(len(events(self.project_id, kind="job_evidence_attached")), 1)

    def test_successful_attach_is_complete_and_idempotent(self):
        job_id, _, contents = self.collected_fixture()
        with patch.object(service, "_remote", side_effect=AssertionError("Import must use existing results")) as remote:
            first = controller.attach_job_evidence(self.project_id, job_id, "verification")
            second = controller.attach_job_evidence(self.project_id, job_id, "verification")
        remote.assert_not_called()
        self.assertEqual(first["evidence_kind"], "toolchain_test")
        self.assertEqual([a["artifact_id"] for a in first["artifacts"]], [a["artifact_id"] for a in second["artifacts"]])
        self.assertEqual(len(workspace.list_artifacts(self.project_id)), len(contents))
        root = workspace.project_root(self.project_id)
        for artifact in first["artifacts"]:
            name = Path(artifact["path"]).name
            self.assertEqual((root / artifact["path"]).read_bytes(), contents[name])
            self.assertEqual(artifact["source_job_id"], job_id)
            self.assertEqual(artifact["evidence_kind"], "toolchain_test")
        metrics = next(a for a in first["artifacts"] if Path(a["path"]).name == "metrics.json")
        self.assertEqual(metrics["kind"], "verification_results")
        (root / metrics["path"]).write_text("modified local attachment")
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, job_id, "verification")

    def test_empty_worker_logs_do_not_block_import(self):
        job_id, folder, _ = self.collected_fixture()
        (folder / "evaluate.log").write_bytes(b"")
        saved = next(row for row in events(self.project_id, kind="job_collected") if row["job_id"] == job_id)
        saved["files"].append({"path": "evaluate.log", "sha256": hashlib.sha256(b"").hexdigest(), "bytes": 0})
        record("job_collected", saved, self.project_id)
        result = controller.attach_job_evidence(self.project_id, job_id)
        self.assertTrue(any(Path(a["path"]).name == "metrics.json" for a in result["artifacts"]))

    def test_project_toolchain_label_dominates_attached_fixture(self):
        job_id, _, _ = self.collected_fixture(independent=True, synthetic=False)
        result = controller.attach_job_evidence(self.project_id, job_id)
        self.assertEqual(result["evidence_kind"], "toolchain_test")
        self.assertTrue(all(a["evidence_kind"] == "toolchain_test" for a in result["artifacts"]))

    def test_attach_requires_the_frozen_proposal_and_its_evaluator(self):
        missing_id, _, _ = self.collected_fixture(synthetic=False)
        submitted = next(row for row in events(self.project_id, kind="job_submitted") if row["job_id"] == missing_id)
        submitted["proposal_id"] = "missing-proposal"
        record("job_submitted", submitted, self.project_id)
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, missing_id)

        mismatch_id, folder, _ = self.collected_fixture(synthetic=False)
        saved = next(row for row in events(self.project_id, kind="job_collected") if row["job_id"] == mismatch_id)
        spec = json.loads((folder / "spec.json").read_text())
        spec["evaluator"] = "different-evaluator.py"
        saved["status"]["metrics_authority"] = "different-evaluator.py"
        # Matching the returned spec and status to one another is insufficient:
        # the actual proposal still freezes evaluate.py.
        (folder / "spec.json").write_text(json.dumps(spec))
        (folder / "status.json").write_text(json.dumps(saved["status"]))
        for file in saved["files"]:
            raw = (folder / file["path"]).read_bytes()
            file.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        record("job_collected", saved, self.project_id)
        with self.assertRaises(ValueError):
            controller.attach_job_evidence(self.project_id, mismatch_id)
        self.assertEqual(workspace.list_artifacts(self.project_id), [])


if __name__ == "__main__":
    unittest.main()
