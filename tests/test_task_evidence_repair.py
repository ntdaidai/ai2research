"""Completed chapter revisions can be handed off without rewriting prior completion or review."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import evidence, service, workspace as w
from tools.harness.state import events, ROOT


class TaskEvidenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        w.start_project("repair-fixture", ["paper"])
        self.project = "repair-fixture"
        self.root = w.project_root(self.project)
        self.task = w.create_task(self.project, "Write setup", "chapter_writer", output_paths=["setup.tex"])

    def artifact(self, relative="setup.tex", content="Original completed chapter wording."):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return w.register_artifact(self.project, relative, "manuscript_chapter")

    def complete(self):
        lease = w.claim_task(self.project, self.task["task_id"], "chapter-agent")
        original = self.artifact()
        w.update_task(self.project, self.task["task_id"], lease["lease_token"], "completed", "Original task complete", [original["artifact_id"]])
        return original

    def test_repair_preserves_original_history_and_old_review_but_unblocks_dependencies(self):
        original = self.complete()
        packet = evidence.prepare_review(self.project, "manuscript", [original["artifact_id"]])
        review = {"score": 8, "confidence": .9, "blockers": [], "issues": [], "evidence_references": [original["artifact_id"]]}
        evidence.record_review(self.project, packet["packet_id"], "agent", review, "gpt", "openai")
        next_task = w.create_task(self.project, "Write results", "chapter_writer", [self.task["task_id"]], output_paths=["results.tex"])
        revised = self.artifact(content="Corrected VisA candidate wording and the top ten percent score definition.")
        with self.assertRaisesRegex(ValueError, "changed"):
            w.claim_task(self.project, next_task["task_id"], "results-agent")
        repaired = service.repair_changed_task_evidence(self.project, self.task["task_id"], [revised["artifact_id"]], "Correct candidate-only VisA description and scoring fraction")
        self.assertEqual(repaired["state"], "completed")
        self.assertEqual(repaired["artifact_ids"], [revised["artifact_id"]])
        self.assertEqual(len(w.list_artifacts(self.project)), 2)
        repair = events(self.project, kind="workflow_task_evidence_repaired")[0]
        self.assertEqual(repair["old_artifact_ids"], [original["artifact_id"]])
        self.assertEqual(repair["new_artifact_ids"], [revised["artifact_id"]])
        old_status = evidence.review_status(self.project, packet["packet_id"])
        self.assertEqual(old_status["state"], "revise")
        self.assertEqual(old_status["reviews"]["agent"]["review"], review)
        self.assertEqual(w.claim_task(self.project, next_task["task_id"], "results-agent")["state"], "running")

    def test_repair_cannot_complete_pending_running_or_failed_tasks(self):
        artifact = self.artifact()
        for phase in ("pending", "running", "failed"):
            with self.subTest(phase=phase):
                if phase == "running":
                    lease = w.claim_task(self.project, self.task["task_id"], "writer")
                elif phase == "failed":
                    w.update_task(self.project, self.task["task_id"], lease["lease_token"], "failed", "Observed draft failure")
                with self.assertRaisesRegex(ValueError, "already completed"):
                    w.repair_changed_task_evidence(self.project, self.task["task_id"], [artifact["artifact_id"]], "Attempted repair")
        self.assertEqual(events(self.project, kind="workflow_task_evidence_repaired"), [])

    def test_repair_requires_summary_current_versions_and_exact_project_paths(self):
        original = self.complete()
        revised = self.artifact(content="Revised wording.")
        for summary in ("", " ", None):
            with self.assertRaisesRegex(ValueError, "summary"):
                w.repair_changed_task_evidence(self.project, self.task["task_id"], [revised["artifact_id"]], summary)
        with self.assertRaisesRegex(ValueError, "latest"):
            w.repair_changed_task_evidence(self.project, self.task["task_id"], [original["artifact_id"]], "Old version")
        wrong = self.artifact("other.tex", "Wrong task output")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            w.repair_changed_task_evidence(self.project, self.task["task_id"], [wrong["artifact_id"]], "Wrong path")
        w.start_project("foreign-project", ["paper"])
        foreign_root = w.project_root("foreign-project")
        (foreign_root / "setup.tex").write_text("Foreign project")
        foreign = w.register_artifact("foreign-project", "setup.tex", "manuscript_chapter")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            w.repair_changed_task_evidence(self.project, self.task["task_id"], [foreign["artifact_id"]], "Foreign output")
        (self.root / "setup.tex").write_text("Unregistered tampering")
        with self.assertRaisesRegex(ValueError, "changed"):
            w.repair_changed_task_evidence(self.project, self.task["task_id"], [revised["artifact_id"]], "Tampered output")

    def test_unspecified_outputs_can_only_rebind_original_paths(self):
        task = w.create_task(self.project, "Original unpathed task", "analysis")
        lease = w.claim_task(self.project, task["task_id"], "worker")
        original = self.artifact("analysis.md", "Completed analysis")
        w.update_task(self.project, task["task_id"], lease["lease_token"], "completed", artifact_ids=[original["artifact_id"]])
        revised = self.artifact("analysis.md", "Corrected analysis")
        repaired = w.repair_changed_task_evidence(self.project, task["task_id"], [revised["artifact_id"]], "Correct recorded analysis")
        self.assertEqual(repaired["artifact_ids"], [revised["artifact_id"]])
        wrong = self.artifact("replacement.md", "Another analysis")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            w.repair_changed_task_evidence(self.project, task["task_id"], [wrong["artifact_id"]], "Cannot change output contract")

    def test_cli_repair_is_available(self):
        self.complete()
        revised = self.artifact(content="Revised completed setup chapter")
        args = {"project_id": self.project, "task_id": self.task["task_id"], "artifact_ids": [revised["artifact_id"]], "summary": "Correct setup wording"}
        result = subprocess.run([str(ROOT / "ai2research"), "repair_changed_task_evidence", json.dumps(args)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["artifact_ids"], [revised["artifact_id"]])


if __name__ == "__main__":
    unittest.main()
