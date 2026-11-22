"""Text-only reviewer boundaries do not rewrite or suppress the actual external critique."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import evidence, service, workspace
from tools.harness.state import config, record, events


class ExternalReviewPromptTests(unittest.TestCase):
    def test_prompt_limits_claimed_capabilities_and_preserves_selected_text(self):
        packet = {"stage": "experiment", "artifacts": [{"artifact_id": "actual-id", "content": "Actual selected source text."}]}
        prompt = evidence._external_prompt(packet)
        instructions, supplied = prompt.split("\n", 1)
        self.assertEqual(json.loads(supplied), packet)
        for boundary in ("no shell, filesystem, working-tree", "recomputed hashes", "missing coverage",
                         "confirmed contradiction", "actual supporting artifact_id", "exact short quotation",
                         "supplied JSON field/value", "Do not invent paths"):
            self.assertIn(boundary, instructions)
        self.assertIn("evidence_locations", instructions)
        self.assertIn("compactness must not suppress", instructions)
        self.assertIn("issues contains actual unresolved defects", instructions)
        self.assertIn("verified_checks and resolved_issues", instructions)
        self.assertIn("affirmative in-scope claim actually made", instructions)
        self.assertIn("a hypothetical stronger claim", instructions)
        self.assertIn("different procedures, precisions and units", instructions)
        self.assertIn("job IDs, filenames", instructions)

    def test_new_prompt_preserves_negative_review_and_does_not_require_new_schema(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            project = "prompt-boundary-fixture"
            workspace.start_project(project, ["isolated review fixture"])
            root = workspace.project_root(project)
            (root / "source.md").write_text("Claim: independent code verification is pending.")
            artifact = workspace.register_artifact(project, "source.md", "report")
            packet = evidence.prepare_review(project, "manuscript", [artifact["artifact_id"]])
            critique = {"score": 3, "confidence": .8, "blockers": ["Independent code verification missing"],
                        "issues": [{"severity": "major", "description": "Unverified implementation claim"}],
                        "evidence_references": [artifact["artifact_id"]]}
            cfg = config()["review"]
            receipt = record("review_result", {"request_id": "real-receipt-fixture", "provider": cfg["provider"],
                "requested_model": cfg["model"], "actual_model": cfg["model"], "finish_reason": "stop",
                "response": json.dumps(critique)}, project)
            receipt_before = next(x for x in events(project, kind="review_result") if x["event_id"] == receipt["event_id"])
            with patch.object(service, "review_topic", return_value=receipt) as external:
                self.assertEqual(evidence.run_external_review(project, packet["packet_id"], 512)["state"], "completed")
                self.assertIn("You have no shell", external.call_args.args[0])
                evidence.run_external_review(project, packet["packet_id"], 512)
                self.assertEqual(external.call_count, 1)
            status = evidence.review_status(project, packet["packet_id"])
            self.assertEqual(status["state"], "revise")
            self.assertEqual(status["reviews"]["deepseek"]["review"], critique)
            receipt_after = next(x for x in events(project, kind="review_result") if x["event_id"] == receipt["event_id"])
            self.assertEqual(receipt_before, receipt_after)


if __name__ == "__main__":
    unittest.main()
