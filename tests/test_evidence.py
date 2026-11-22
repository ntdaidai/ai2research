import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from harness import evidence, service
from harness.state import config, record
from harness.workspace import start_project, project_root, register_artifact


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        start_project("review-test", ["https://arxiv.org/abs/1706.03762"])
        self.project = "review-test"
        self.root = project_root(self.project)
        (self.root / "results.md").write_text("Baseline 1.0, candidate 0.9; independent replication pending", encoding="utf-8")
        self.artifact = register_artifact(self.project, "results.md", "results")
        self.packet = evidence.prepare_review(self.project, "manuscript", [self.artifact["artifact_id"]])

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def review(self, **changes):
        return {"score": 8, "confidence": 0.8, "blockers": [], "issues": [],
                "evidence_references": [self.artifact["artifact_id"]], **changes}

    def external(self, value=None, finish="stop"):
        cfg = config()["review"]
        return record("review_result", {"request_id": "actual-request", "provider": cfg["provider"],
                      "requested_model": cfg["model"], "actual_model": cfg["model"],
                      "finish_reason": finish, "response": json.dumps(value or self.review())}, self.project)

    def test_same_packet_is_idempotent_and_requires_two_reviews(self):
        same = evidence.prepare_review(self.project, "manuscript", [self.artifact["artifact_id"]])
        self.assertEqual(same["packet_id"], self.packet["packet_id"])
        self.assertEqual(evidence.review_status(self.project, same["packet_id"])["state"], "pending")
        evidence.record_review(self.project, same["packet_id"], "agent", self.review(), "gpt-6", "openai")
        self.assertEqual(evidence.review_status(self.project, same["packet_id"])["missing_reviewers"], ["deepseek"])
        with patch.object(service, "review_topic", side_effect=lambda *args: self.external()) as call:
            self.assertEqual(evidence.run_external_review(self.project, same["packet_id"], 512)["state"], "completed")
            evidence.run_external_review(self.project, same["packet_id"], 512)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(evidence.review_status(self.project, same["packet_id"])["state"], "ready")
        (self.root / "results.md").write_text("changed", encoding="utf-8")
        self.assertEqual(evidence.review_status(self.project, same["packet_id"])["state"], "revise")

    def test_local_deepseek_and_cross_project_reviews_rejected(self):
        with self.assertRaises(ValueError):
            evidence.record_review(self.project, self.packet["packet_id"], "deepseek", self.review(), "deepseek", "aliyun")
        start_project("other-project", ["paper"])
        with self.assertRaises(ValueError):
            evidence.review_status("other-project", self.packet["packet_id"])

    def test_synthetic_tampered_and_oversized_evidence_rejected(self):
        synthetic = register_artifact(self.project, "results.md", "results", "toolchain_test")
        with self.assertRaises(ValueError):
            evidence.prepare_review(self.project, "experiment", [synthetic["artifact_id"]])
        (self.root / "results.md").write_text("different", encoding="utf-8")
        with self.assertRaises(ValueError):
            evidence.prepare_review(self.project, "experiment", [self.artifact["artifact_id"]])
        (self.root / "large.md").write_text("x" * 17000)
        large = register_artifact(self.project, "large.md", "results")
        cfg = config()
        bounded = {**cfg, "review": {**cfg["review"], "max_packet_chars": 16000}}
        with patch.object(evidence, "config", return_value=bounded):
            with self.assertRaisesRegex(ValueError, "16000 characters.*No content was truncated"):
                evidence.prepare_review(self.project, "experiment", [large["artifact_id"]])

    def test_configured_large_packet_preserves_full_content_and_input_limit_is_explicit(self):
        content = "Actual complete implementation text.\n" * 700
        (self.root / "complete-source.md").write_text(content)
        artifact = register_artifact(self.project, "complete-source.md", "framework_evidence", "toolchain_test")
        cfg = config()
        expanded = {**cfg, "review": {**cfg["review"], "max_packet_chars": 100000, "max_input_chars": 120000}}
        with patch.object(evidence, "config", return_value=expanded):
            packet = evidence.prepare_review(self.project, "framework", [artifact["artifact_id"]])
            self.assertEqual(packet["artifacts"][0]["content"], content)
            self.assertIn("Full content of selected files only", packet["coverage"])
            review = self.review(evidence_references=[artifact["artifact_id"]])
            with patch.object(service, "review_topic", side_effect=lambda *args: self.external(review)) as paid:
                result = evidence.run_external_review(self.project, packet["packet_id"], 512)
                self.assertEqual(result["state"], "completed")
                sent_packet = json.loads(paid.call_args.args[0].split("\n", 1)[1])
                self.assertEqual(sent_packet["artifacts"][0]["content"], content)
        limited = {**expanded, "review": {**expanded["review"], "max_input_chars": 1000}}
        with patch.object(evidence, "config", return_value=limited), patch.object(service, "review_topic") as paid:
            with self.assertRaisesRegex(ValueError, "1000 characters.*No content was truncated"):
                evidence.run_external_review(self.project, packet["packet_id"], 512)
            paid.assert_not_called()

    def test_packet_size_error_reports_actual_total_and_top_three_paths_without_content(self):
        marker = "PRIVATE_CONTENT_MARKER_NOT_FOR_DIAGNOSTICS"
        selected = []
        for index, length in enumerate((900, 700, 500, 300)):
            name = f"size-source-{index}.txt"
            (self.root / name).write_text(marker + "x" * length)
            selected.append(register_artifact(self.project, name, "report")["artifact_id"])
        cfg = config()
        high = {**cfg, "review": {**cfg["review"], "max_packet_chars": 100000}}
        with patch.object(evidence, "config", return_value=high):
            packet = evidence.prepare_review(self.project, "manuscript", selected)
        actual = len(evidence._dump({key: packet[key] for key in ("stage", "rubric", "artifacts", "round_key")}))
        low = {**cfg, "review": {**cfg["review"], "max_packet_chars": 500}}
        before = len(evidence.list_review_packets(self.project))
        with patch.object(evidence, "config", return_value=low), patch.object(service, "review_topic") as external:
            with self.assertRaises(ValueError) as caught:
                evidence.prepare_review(self.project, "manuscript", selected)
            external.assert_not_called()
        message = str(caught.exception)
        self.assertIn("exceeds 500 characters", message)
        self.assertIn(f"actual serialized characters: {actual}", message)
        for index, length in enumerate((900, 700, 500)):
            self.assertIn(f'"path": "size-source-{index}.txt"', message)
            self.assertIn(f'"content_characters": {len(marker)+length}', message)
        self.assertNotIn("size-source-3.txt", message)
        self.assertNotIn(marker, message)
        self.assertIn("No content was truncated", message)
        self.assertEqual(len(evidence.list_review_packets(self.project)), before)

    def test_schema_and_unresolvable_references_rejected(self):
        for review in ({"score": 9}, self.review(score=True), self.review(confidence=3),
                       self.review(evidence_references=["invented"]), self.review(issues=[{"description": "x"}])):
            with self.assertRaises(ValueError):
                evidence.record_review(self.project, self.packet["packet_id"], "agent", review, "gpt-6", "openai")

    def test_truncated_or_missing_api_receipt_never_qualifies(self):
        with patch.object(service, "review_topic", side_effect=lambda *args: self.external(finish="length")) as call:
            result = evidence.run_external_review(self.project, self.packet["packet_id"], 512)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(evidence.run_external_review(self.project, self.packet["packet_id"], 512)["state"], "failed")
            self.assertEqual(call.call_count, 1)
        self.assertIn("deepseek", evidence.review_status(self.project, self.packet["packet_id"])["missing_reviewers"])
        new_packet = evidence.prepare_review(self.project, "experiment", [self.artifact["artifact_id"]], {"round": 2})
        forged = {"kind": "review_result", "event_id": "made-up", "request_id": "made-up", "finish_reason": "stop"}
        with patch.object(service, "review_topic", return_value=forged):
            self.assertEqual(evidence.run_external_review(self.project, new_packet["packet_id"], 512)["state"], "failed")

    def test_concurrent_external_calls_are_reserved_once(self):
        entered, release = threading.Event(), threading.Event()
        def call(*args):
            entered.set()
            release.wait(10)
            return self.external()
        with patch.object(service, "review_topic", side_effect=call) as paid:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(evidence.run_external_review, self.project, self.packet["packet_id"], 512)
                self.assertTrue(entered.wait(5))
                second = pool.submit(evidence.run_external_review, self.project, self.packet["packet_id"], 512)
                self.assertEqual(second.result(timeout=5)["state"], "in_progress")
                release.set()
                self.assertEqual(first.result(timeout=5)["state"], "completed")
            self.assertEqual(paid.call_count, 1)

    def test_review_problems_create_idempotent_revision_tasks(self):
        evidence.record_review(self.project, self.packet["packet_id"], "agent", self.review(blockers=["Need independent seeds"]), "gpt-6", "openai")
        self.assertEqual(evidence.review_status(self.project, self.packet["packet_id"])["state"], "revise")
        first = evidence.create_revision_tasks(self.project, self.packet["packet_id"])
        second = evidence.create_revision_tasks(self.project, self.packet["packet_id"])
        self.assertEqual(first["tasks"][0]["task_id"], second["tasks"][0]["task_id"])
        self.assertEqual(first["tasks"][0]["state"], "pending")

    def test_major_concern_and_unverified_experiment_block_ready(self):
        evidence.record_review(self.project, self.packet["packet_id"], "agent",
                               self.review(issues=[{"severity": "major", "description": "Missing fair comparison"}]), "gpt-6", "openai")
        self.assertEqual(evidence.review_status(self.project, self.packet["packet_id"])["state"], "revise")
        packet = evidence.prepare_review(self.project, "experiment", [self.artifact["artifact_id"]])
        evidence.record_review(self.project, packet["packet_id"], "agent", self.review(), "gpt-6", "openai")
        with patch.object(service, "review_topic", side_effect=lambda *args: self.external()):
            evidence.run_external_review(self.project, packet["packet_id"], 512)
        status = evidence.review_status(self.project, packet["packet_id"])
        self.assertEqual(status["state"], "revise")
        self.assertTrue(status["gate_blockers"])
        self.assertFalse(evidence.validate_result_artifact(self.project, self.artifact["artifact_id"])["valid"])

    def test_explicit_review_rounds_are_bounded_without_resetting_history(self):
        first = self.packet["packet_id"]
        second = evidence.prepare_review(self.project, "manuscript", [self.artifact["artifact_id"]], round_key="revision-1")
        third = evidence.prepare_review(self.project, "manuscript", [self.artifact["artifact_id"]], round_key="revision-2")
        self.assertNotEqual(first, second["packet_id"])
        self.assertNotEqual(second["packet_id"], third["packet_id"])
        with self.assertRaisesRegex(ValueError, "Three review rounds"):
            evidence.prepare_review(self.project, "manuscript", [self.artifact["artifact_id"]], round_key="revision-3")
        self.assertEqual(len(evidence.list_review_packets(self.project)), 3)

    def test_framework_review_accepts_toolchain_evidence_without_research_relabeling(self):
        start_project("framework-test", ["engineering acceptance"], evidence_kind="toolchain_test")
        root = project_root("framework-test")
        (root / "implementation.md").write_text("Actual engineering implementation and test evidence.")
        artifact = register_artifact("framework-test", "implementation.md", "framework_evidence")
        self.assertEqual(artifact["evidence_kind"], "toolchain_test")
        packet = evidence.prepare_review("framework-test", "framework", [artifact["artifact_id"]])
        report = evidence.review_status("framework-test", packet["packet_id"])
        self.assertEqual(report["stage"], "framework")
        self.assertEqual(report["state"], "pending")
        self.assertEqual(report["gate_blockers"], [])
        self.assertIn("Engineering framework", report["meaning"])
        self.assertEqual(register_artifact("framework-test", "implementation.md", "framework_evidence")["evidence_kind"], "toolchain_test")

    def test_framework_toolchain_packet_cannot_satisfy_research_stages(self):
        artifact = register_artifact(self.project, "results.md", "framework_evidence", "toolchain_test")
        packet = evidence.prepare_review(self.project, "framework", [artifact["artifact_id"]])
        for stage in ("experiment", "manuscript"):
            with self.assertRaisesRegex(ValueError, "Research review requires research evidence"):
                evidence.prepare_review(self.project, stage, [artifact["artifact_id"]])
        evidence.record_review(self.project, packet["packet_id"], "agent",
                               self.review(evidence_references=[artifact["artifact_id"]]), "gpt-6", "openai")
        framework_review = self.review(evidence_references=[artifact["artifact_id"]])
        with patch.object(service, "review_topic", side_effect=lambda *args: self.external(framework_review)):
            self.assertEqual(evidence.run_external_review(self.project, packet["packet_id"], 512)["state"], "completed")
        status = evidence.review_status(self.project, packet["packet_id"])
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["stage"], "framework")
        self.assertIn("cannot satisfy experiment/manuscript", status["meaning"])

    def test_interrupted_external_call_recovers_receipt_without_spending_again(self):
        packet_id = self.packet["packet_id"]
        prompt = evidence._external_prompt(self.packet)
        with evidence._db() as db:
            db.execute("INSERT INTO external_review_calls VALUES (?,?,?)", (packet_id, self.project,
                       json.dumps({"packet_id": packet_id, "state": "in_progress", "created_utc": "2000-01-01T00:00:00+00:00"})))
        record("review_input", {"request_id": "actual-request", "topic": prompt}, self.project)
        self.external()
        with patch.object(service, "review_topic") as paid:
            resumed = evidence.run_external_review(self.project, packet_id, 512)
            self.assertEqual(resumed["state"], "completed")
            paid.assert_not_called()

    def test_json_string_control_character_recovery_preserves_negative_review(self):
        packet_id = self.packet["packet_id"]
        negative = self.review(score=6.5, issues=[{"severity": "major", "description": "First line\nsecond line"}])
        raw = json.dumps(negative).replace("First line\\nsecond line", "First line\nsecond line")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        failed = {"packet_id": packet_id, "state": "failed", "failed_phase": "response_json", "error_type": "JSONDecodeError"}
        with evidence._db() as db:
            db.execute("INSERT INTO external_review_calls VALUES (?,?,?)", (packet_id, self.project, json.dumps(failed)))
        record("external_packet_review", failed, self.project)
        record("review_request", {}, self.project)
        record("review_input", {"request_id": "actual-request", "topic": evidence._external_prompt(self.packet)}, self.project)
        cfg = config()["review"]
        receipt = record("review_result", {"request_id": "actual-request", "provider": cfg["provider"],
            "requested_model": cfg["model"], "actual_model": cfg["model"], "finish_reason": "stop", "response": raw}, self.project)
        before = service.review_budget()["used_calls"]
        with patch.object(service, "review_topic") as paid:
            recovered = evidence.run_external_review(self.project, packet_id, 512)
            self.assertEqual(recovered["state"], "completed")
            self.assertEqual(recovered["review"]["review"], negative)
            self.assertEqual(recovered["review"]["provenance"]["event_id"], receipt["event_id"])
            self.assertEqual(service.review_budget()["used_calls"], before)
            self.assertEqual(evidence.review_status(self.project, packet_id)["state"], "revise")
            evidence.run_external_review(self.project, packet_id, 512)
            paid.assert_not_called()

    def test_json_failure_without_original_receipt_never_resends(self):
        failed = {"packet_id": self.packet["packet_id"], "state": "failed", "failed_phase": "response_json", "error_type": "JSONDecodeError"}
        with evidence._db() as db:
            db.execute("INSERT INTO external_review_calls VALUES (?,?,?)", (self.packet["packet_id"], self.project, json.dumps(failed)))
        with patch.object(service, "review_topic") as paid:
            result = evidence.run_external_review(self.project, self.packet["packet_id"], 512)
            self.assertEqual(result["state"], "failed")
            paid.assert_not_called()

    def test_one_outside_apostrophe_repair_preserves_all_negative_verdict_fields(self):
        negative = self.review(score=3, blockers=["Missing independent run"],
            issues=[{"severity": "major", "description": "Operator's unverified claim\nremains unresolved"}])
        raw = json.dumps(negative).replace('}], "evidence_references"', '}\' ], "evidence_references"')
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        restored, repair = evidence._parse_external_review_response(raw)
        self.assertEqual(restored, negative)
        self.assertEqual(raw[repair["json_text_offset"]], "'")
        self.assertEqual(repair["operation"], "remove_one_stray_apostrophe_outside_JSON_strings")
        evidence.record_review(self.project, self.packet["packet_id"], "agent", restored, "actual-fixture-agent", "test")
        self.assertEqual(evidence.review_status(self.project, self.packet["packet_id"])["state"], "revise")
        valid, no_repair = evidence._parse_external_review_response(json.dumps(negative))
        self.assertEqual(valid, negative)
        self.assertIsNone(no_repair)

    def test_other_JSON_corruption_is_not_silently_repaired(self):
        for raw in ['{"score": 7} trailing', '{"score": "operator\'s}', '{"issues": [{}\'\' ], "score":7}',
                    '{"issues": [{}\' ], "score":seven}', '{"score":7, "issues":']:
            with self.subTest(raw=raw), self.assertRaises(json.JSONDecodeError):
                evidence._parse_external_review_response(raw)

    def test_matrix_requires_real_proposal_and_independent_metrics(self):
        from harness.experiments import prepare_experiment
        (self.root / "train.py").write_text("print('method')")
        (self.root / "evaluate.py").write_text("print('independent evaluation')")
        proposal = prepare_experiment(self.project, "baseline", str(self.root / "train.py"), input_files=["evaluate.py"],
                                      evaluation_files=["evaluate.py"], purpose="research")
        identifiers = [uuid.uuid4().hex, uuid.uuid4().hex]
        for i, identifier in enumerate(identifiers):
            record("job_submitted", {"job_id": identifier, "proposal_id": proposal["proposal_id"], "approval_id": "test-scope",
                   "spec": {"kind": "approved_gpu_experiment", "manifest_sha256": proposal["manifest_sha256"]}}, self.project)
            record("job_collected", {"job_id": identifier, "status": {"state": "completed", "job_kind": "approved_gpu_experiment",
                   "metrics_authority": "evaluate.py"}, "metrics": {"loss": 1.0 - i * 0.1}, "metrics_error": None,
                   "mlflow_run_id": "test-run-" + str(i)}, self.project)
        matrix = evidence.build_experiment_matrix(self.project, identifiers[0], [identifiers[1]], "loss")
        self.assertEqual(matrix["comparisons"][0]["decision"], "improved")
        self.assertFalse(matrix["sota_established"])
        self.assertEqual(matrix["verification"], "pending")
        self.assertEqual(len(matrix["follow_up_plan"]), 3)
        matrix_validation = evidence.validate_result_artifact(self.project, matrix["artifact"]["artifact_id"])
        self.assertTrue(matrix_validation["valid"])
        self.assertEqual(matrix_validation["job_ids"], identifiers)
        verified = evidence.prepare_review(self.project, "experiment", [matrix["artifact"]["artifact_id"]])
        self.assertEqual(evidence.review_status(self.project, verified["packet_id"])["gate_blockers"], [])
        # The actual worker emits wrapped metrics without job_id; a collected
        # file digest and source_job_id provide provenance for attached evidence.
        raw_metrics = json.dumps({"metrics": {"loss": 0.9}})
        (self.root / "collected-metrics.json").write_text(raw_metrics)
        artifact = register_artifact(self.project, "collected-metrics.json", "experiment_result", source_job_id=identifiers[1])
        record("job_collected", {"job_id": identifiers[1], "status": {"state": "completed", "job_kind": "approved_gpu_experiment",
               "metrics_authority": "evaluate.py"}, "metrics": {"loss": 0.9}, "metrics_error": None,
               "mlflow_run_id": "test-run-1", "files": [{"path": "metrics.json", "sha256": artifact["sha256"]}]}, self.project)
        raw_packet = evidence.prepare_review(self.project, "experiment", [artifact["artifact_id"]])
        self.assertEqual(evidence.review_status(self.project, raw_packet["packet_id"])["gate_blockers"], [])
        self.assertEqual(evidence.validate_result_artifact(self.project, artifact["artifact_id"])["job_ids"], [identifiers[1]])
        # A forged self-reported result cannot replace the independently evaluated metric.
        record("job_collected", {"job_id": identifiers[1], "status": {"state": "completed", "job_kind": "approved_gpu_experiment",
               "metrics_authority": "train.py"}, "metrics": {"loss": 0.0}, "metrics_error": None,
               "mlflow_run_id": "test-run-forged"}, self.project)
        with self.assertRaisesRegex(ValueError, "independent evaluator"):
            evidence.build_experiment_matrix(self.project, identifiers[0], [identifiers[1]], "loss")


if __name__ == "__main__":
    unittest.main()
