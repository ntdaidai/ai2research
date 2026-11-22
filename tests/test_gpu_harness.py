import concurrent.futures
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from harness import experiments, remote_worker, service


class GPUContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.temp.name})
        self.env.start()
        self.script = Path(self.temp.name) / "train.py"
        self.script.write_text("print('unit fixture, never executed')")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def proposal(self):
        return experiments.prepare_experiment("unit-test", "unit plan", str(self.script))

    def test_gpu_policy_preserves_headroom_and_rejects_busy_devices(self):
        inventory = remote_worker.parse_inventory("0, GPU-a, 0000:01:00.0, RTX 3090, 24576, 10000, 95\n1, GPU-b, 0000:02:00.0, RTX 3090, 24576, 2500, 0\n2, GPU-c, 0000:03:00.0, RTX 3090, 24576, 9000, 0\n")
        policy = {"memory_mb": 2048, "reserve_mb": 1024, "allowed_indices": [0, 1, 2]}
        self.assertEqual([g["index"] for g in remote_worker.eligible_gpus(inventory, policy)], [2])
        policy["allowed_indices"] = [1]
        self.assertEqual(remote_worker.eligible_gpus(inventory, policy), [])

    def test_modified_frozen_plan_never_reaches_server(self):
        proposal = self.proposal()
        approval = experiments.approve_proposal(proposal["proposal_id"], "unit fixture approval")
        path = Path(proposal["manifest_path"])
        manifest = json.loads(path.read_text())
        manifest["resources"]["gpu"]["memory_mb"] = 22000
        path.write_text(json.dumps(manifest))
        with patch.object(service, "_remote") as remote:
            with self.assertRaises(ValueError):
                experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])
            remote.assert_not_called()

    def test_approval_has_atomic_run_limit(self):
        proposal = self.proposal()
        approval = experiments.approve_proposal(proposal["proposal_id"], "unit fixture approval", max_runs=1)
        def submit(_):
            try:
                return experiments.submit_proposal(proposal["proposal_id"], approval["event_id"])
            except ValueError:
                return None
        with patch.object(service, "_remote", return_value={"state": "mocked_submission"}) as remote:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                result = list(pool.map(submit, range(4)))
            self.assertEqual(sum(r is not None for r in result), 1)
            self.assertEqual(remote.call_count, 1)

    def test_unapproved_gpu_plan_never_reaches_server(self):
        proposal = self.proposal()
        with patch.object(service, "_remote") as remote:
            with self.assertRaises(ValueError):
                experiments.submit_proposal(proposal["proposal_id"], "not-approved")
            remote.assert_not_called()

    def test_pause_blocks_new_preparation_submission_and_redispatch(self):
        proposal = self.proposal()
        approval = experiments.approve_proposal(proposal['proposal_id'], 'unit fixture authorization')
        from harness.state import record
        record('project_execution_control', {'execution_state': 'paused'}, 'unit-test')
        with patch.object(service, '_remote') as remote:
            with self.assertRaisesRegex(ValueError, 'paused'):
                self.proposal()
            with self.assertRaisesRegex(ValueError, 'paused'):
                experiments.submit_proposal(proposal['proposal_id'], approval['event_id'])
            remote.assert_not_called()
        from harness.state import connect
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='job_submitted'").fetchone()[0], 0)

    def test_frozen_cpu_research_preserves_evaluation_and_device_scope(self):
        evaluator = self.script.parent / 'evaluate.py'
        evaluator.write_text('# independent frozen evaluator fixture')
        args = dict(project_id='unit-test', title='bounded CPU research', script_path=str(self.script),
                    gpu_memory_mb=0, input_files=['evaluate.py'], evaluation_files=['evaluate.py'])
        plan = experiments.prepare_experiment(**args)
        approval = experiments.approve_proposal(plan['proposal_id'], 'unit CPU fixture authorization', 2, True, [])
        manifest = json.loads(Path(plan['manifest_path']).read_text())
        self.assertIsNone(manifest['resources']['gpu'])
        self.assertEqual(manifest['resources']['evaluator'], 'evaluate.py')
        with patch.object(service, '_remote', return_value={'state': 'mocked'}) as remote:
            experiments.submit_proposal(plan['proposal_id'], approval['event_id'])
            spec = remote.call_args.args[0]['spec']
            self.assertEqual(spec['kind'], 'approved_cpu_experiment')
            self.assertIsNone(spec['gpu'])
            args.update(parent_proposal_id=plan['proposal_id'], gpu_memory_mb=1024)
            variant = experiments.prepare_experiment(**args)
            with self.assertRaisesRegex(ValueError, 'resources'):
                experiments.submit_proposal(variant['proposal_id'], approval['event_id'])
            self.assertEqual(remote.call_count, 1)
        with self.assertRaisesRegex(ValueError, 'CPU proposals'):
            experiments.prepare_experiment(**{**args, 'gpu_memory_mb': 0, 'evaluation_on_gpu': True})

    def test_collection_rejects_symlinks_traversal_and_nonfinite_metrics(self):
        folder = Path(self.temp.name)
        with self.assertRaises(ValueError):
            remote_worker.safe_file(folder, "../other-user")
        (folder / "spec.json").write_text(json.dumps({"kind": "synthetic_gpu_toolchain_test"}))
        (folder / "metrics.json").write_text('{"metrics":{"accuracy":NaN}}')
        result = remote_worker.collect_result(folder, {"state": "completed"})
        self.assertEqual(result["metrics"], {})
        self.assertIsNotNone(result["metrics_error"])

    def test_checksum_tampering_never_reaches_mlflow(self):
        job_id = "a" * 32
        from harness.state import record
        record("job_submitted", {"job_id": job_id})
        payload = {"job_id": job_id, "status": {"job_id": job_id, "state": "completed"}, "metrics": {}, "metrics_error": None,
                   "files": [{"path": "script.py", "base64": "cHJpbnQoMSk=", "sha256": "0" * 64, "bytes": 8}]}
        with patch.object(service, "_remote", return_value=payload):
            with self.assertRaisesRegex(ValueError, "checksum"):
                experiments.collect_job_results(job_id)

    def test_authorized_code_iteration_keeps_evaluator_and_dataset_fixed(self):
        evaluator = self.script.parent / "evaluate.py"
        evaluator.write_text("# frozen evaluation fixture")
        args = {"project_id": "unit-test", "title": "bounded search", "script_path": str(self.script),
                "input_files": ["evaluate.py"], "evaluation_files": ["evaluate.py"],
                "parameters": {"dataset_sha": "fixed", "lr": 0.1}}
        root = experiments.prepare_experiment(**args)
        approval = experiments.approve_proposal(root["proposal_id"], "unit fixture bounded search", 2, True, ["lr"])
        self.script.write_text("print('method variant, never executed')")
        args.update(parent_proposal_id=root["proposal_id"], parameters={"dataset_sha": "fixed", "lr": 0.01})
        variant = experiments.prepare_experiment(**args)
        with patch.object(service, "_remote", return_value={"state": "mocked"}) as remote:
            experiments.submit_proposal(variant["proposal_id"], approval["event_id"])
            self.assertEqual(remote.call_count, 1)
            args["parameters"] = {"dataset_sha": "changed", "lr": 0.01}
            invalid = experiments.prepare_experiment(**args)
            with self.assertRaises(ValueError):
                experiments.submit_proposal(invalid["proposal_id"], approval["event_id"])
            evaluator.write_text("# changed evaluator")
            args["parameters"] = {"dataset_sha": "fixed", "lr": 0.01}
            invalid = experiments.prepare_experiment(**args)
            with self.assertRaises(ValueError):
                experiments.submit_proposal(invalid["proposal_id"], approval["event_id"])
            self.assertEqual(remote.call_count, 1)

    def test_trial_comparison_rejects_failed_or_foreign_results(self):
        from harness.state import record
        ids = ["b" * 32, "c" * 32]
        for job_id, loss in zip(ids, (0.8, 0.7)):
            record("job_submitted", {"job_id": job_id, "approval_id": "same-scope"}, "unit-test")
            record("job_collected", {"job_id": job_id, "metrics": {"loss": loss}, "metrics_error": None,
                                    "status": {"state": "completed"}, "mlflow_run_id": "fixture"}, "unit-test")
        result = experiments.compare_jobs(*ids, "loss")
        self.assertEqual(result["decision"], "improved")
        record("job_collected", {"job_id": ids[1], "metrics": {"loss": 0}, "metrics_error": None,
                                "status": {"state": "failed"}, "mlflow_run_id": "fixture"}, "unit-test")
        with self.assertRaises(ValueError):
            experiments.compare_jobs(*ids, "loss")
        foreign = "d" * 32
        record("job_submitted", {"job_id": foreign}, "another-direction")
        with self.assertRaises(ValueError):
            experiments.compare_jobs(ids[0], foreign, "loss")


if __name__ == "__main__":
    unittest.main()
