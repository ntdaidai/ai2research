"""Selected paper delivery retains scientific gates and an explicit omitted-history index."""
import json
import os
import tempfile
import unittest
import zipfile
import subprocess
from pathlib import Path
from unittest.mock import patch

from tools.harness import publication, service, workspace
from tools.harness.state import ROOT


class DeliveryScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = "scope-fixture"
        workspace.start_project(self.project, ["isolated delivery fixture"], "Scope Fixture")
        self.root = workspace.project_root(self.project)
        self.plan = publication.publication_plan(self.project)
        for name, chapter in self.plan["chapters"].items():
            lease = workspace.claim_task(self.project, chapter["task_id"], name)
            art = self.artifact(chapter["path"], "A completed isolated chapter fixture with sufficient source text for publication assembly.", "manuscript_chapter")
            workspace.update_task(self.project, chapter["task_id"], lease["lease_token"], "completed", artifact_ids=[art["artifact_id"]])
        self.artifact("paper/references.bib", "@article{fixture,title={Fixture Source},year={2020}}", "bibliography")
        self.history = self.artifact("history/rejected-branch.py", "# historical branch; excluded from this paper", "source_code")
        self.evidence = [
            self.artifact("code/method.py", "# selected primary method fixture", "source_code"),
            self.artifact("results/main.json", '{"metrics":{"accuracy":0.9}}', "experiment_result", "main-job"),
            self.artifact("results/verify.json", '{"metrics":{"accuracy":0.9}}', "verification_results", "verify-job"),
            self.artifact("results/ablation.json", '{"metrics":{"accuracy":0.8}}', "ablation_results", "ablation-job"),
            self.artifact("analysis/negative-results.md", "Rejected-branch evidence and limitations are retained explicitly.", "negative_results_summary"),
        ]
        self.artifact("vendor/dependency.py", "# upstream dependency identity, not a claim of full external source review", "source_dependency")
        self.artifact("requirements.txt", "example==1.0", "dependency_lock")
        self.artifact("REPRODUCE.md", "Reproduction fixture; no experiment or review was executed by these tests.", "reproduction_readme")
        self.artifact("figures/reproduce.py", "# standalone figure source fixture", "figure_source")
        self.artifact("figures/diagram.pdf", "%PDF-1.4\nrendered figure fixture", "diagram_pdf")
        self.context = {"title": "Selected Paper", "delivery_scope": {
            "evidence_artifact_ids": [a["artifact_id"] for a in self.evidence]}}
        self.artifact("paper/context.json", json.dumps(self.context), "publication_context")
        pdf = Path(self.tmp.name) / "compiled.pdf"
        pdf.write_bytes(b"%PDF-1.4\ncompiled fixture\n")
        with patch("tools.harness.service.compile_paper", return_value={"status": "completed", "pdf": str(pdf)}):
            result = publication.assemble_manuscript(self.project)
            self.assertEqual(result["status"], "completed")
        self.latest = publication._latest_artifacts(self.project)
        self.selected = [a["artifact_id"] for a in self.latest.values() if a["artifact_id"] != self.history["artifact_id"]]

    def artifact(self, relative, text, kind, source_job_id=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return workspace.register_artifact(self.project, relative, kind, source_job_id=source_job_id)

    def test_explicit_export_omits_history_but_does_not_pass_unverified_research(self):
        blocked = service.export_delivery(self.project, artifact_ids=self.selected)
        self.assertEqual(blocked["status"], "incomplete")
        self.assertIsNone(blocked["archive"])
        self.assertTrue(any("main results backed" in missing for missing in blocked["missing"]))
        exported = service.export_delivery(self.project, True, self.selected)
        with zipfile.ZipFile(exported["archive"]) as archive:
            self.assertNotIn(self.history["path"], archive.namelist())
            self.assertIn("analysis/negative-results.md", archive.namelist())
            manifest = json.loads(archive.read("DELIVERY_MANIFEST.json"))
            self.assertEqual(manifest["status"], "incomplete")
            self.assertEqual(manifest["delivery_scope"]["mode"], "explicit")
            omitted = next(x for x in manifest["omitted_history"] if x["artifact_id"] == self.history["artifact_id"])
            self.assertIn("no review pass", omitted["review_coverage"])
            dependency = next(x for x in manifest["files"] if x["kind"] == "source_dependency")
            self.assertIn("full dependency source review is not asserted", dependency["review_coverage"])
            record = json.loads(archive.read("WORKFLOW_RECORD.json"))
            self.assertTrue(any(x["artifact_id"] == self.history["artifact_id"] for x in record["project"]["artifacts"]))
        default = publication.export_delivery(self.project, allow_incomplete=True)
        with zipfile.ZipFile(default["archive"]) as archive:
            self.assertIn(self.history["path"], archive.namelist())

    def test_selected_export_rejects_omitted_paper_evidence_and_build_dependency(self):
        missing_evidence = [i for i in self.selected if i != self.evidence[-1]["artifact_id"]]
        with self.assertRaisesRegex(ValueError, "Declared paper evidence omitted"):
            publication.export_delivery(self.project, True, missing_evidence)
        chapter = self.latest["paper/sections/methods.tex"]["artifact_id"]
        with self.assertRaisesRegex(ValueError, "required paper/build dependency"):
            publication.export_delivery(self.project, True, [i for i in self.selected if i != chapter])
        figure = self.latest["figures/diagram.pdf"]["artifact_id"]
        # The receipt records this rendered input, so it is mandatory even when
        # incomplete export is requested.
        with self.assertRaisesRegex(ValueError, "required paper/build dependency"):
            publication.export_delivery(self.project, True, [i for i in self.selected if i != figure])

    def test_selected_export_rejects_superseded_and_cross_project_ids(self):
        old = self.latest["requirements.txt"]["artifact_id"]
        updated = self.artifact("requirements.txt", "example==2.0", "dependency_lock")
        with self.assertRaisesRegex(ValueError, "superseded"):
            publication.export_delivery(self.project, True, self.selected)
        current = [updated["artifact_id"] if i == old else i for i in self.selected]
        workspace.start_project("other-scope", ["other paper"])
        other = workspace.project_root("other-scope")
        (other / "foreign.py").write_text("# belongs to another project")
        foreign = workspace.register_artifact("other-scope", "foreign.py", "source_code")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            publication.export_delivery(self.project, True, [*current, foreign["artifact_id"]])

    def test_scientific_scope_includes_negative_summary_in_dual_review_coverage(self):
        captured = []
        packets = [{"packet_id": "experiment-review", "stage": "experiment", "artifact_ids": [a["artifact_id"] for a in self.evidence[:-1]]}]
        def status(project_id, packet_id):
            captured.append(packet_id)
            return {"state": "ready"}
        selected = {p: a for p, a in self.latest.items() if a["artifact_id"] in self.selected}
        with patch("tools.harness.evidence.list_review_packets", return_value=packets), patch("tools.harness.evidence.review_status", side_effect=status):
            missing = publication._delivery_readiness(self.project, selected, self.context["delivery_scope"]["evidence_artifact_ids"])
        self.assertIn("experiment review does not cover current source/results", missing)
        self.assertIn("experiment-review", captured)

    def test_selected_scope_can_clear_covered_gates_while_default_history_stays_unreviewed(self):
        selected = {p: a for p, a in self.latest.items() if a["artifact_id"] in self.selected}
        packets = [
            {"packet_id": "selected-experiment", "stage": "experiment", "artifact_ids": [a["artifact_id"] for a in self.evidence]},
            {"packet_id": "selected-manuscript", "stage": "manuscript", "artifact_ids": [a["artifact_id"] for p, a in selected.items()
                if p == publication.RENDER_MANIFEST or Path(p).suffix.lower() in publication.LOCAL_TEX_SUFFIXES]},
        ]
        def validate(_project, identifier):
            artifact = next(a for a in selected.values() if a["artifact_id"] == identifier)
            return {"valid": True, "job_id": artifact["source_job_id"], "job_ids": [artifact["source_job_id"]], "issues": []}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validate), patch("tools.harness.evidence.list_review_packets", return_value=packets), patch("tools.harness.evidence.review_status", return_value={"state": "ready"}):
            exported = publication.export_delivery(self.project, artifact_ids=self.selected)
            self.assertEqual(exported["status"], "finalized")
            default = publication.export_delivery(self.project)
            self.assertEqual(default["status"], "incomplete")
            self.assertIn("experiment review does not cover current source/results", default["missing"])

    def test_cli_accepts_explicit_scope_without_bypassing_real_result_checks(self):
        payload = json.dumps({"project_id": self.project, "artifact_ids": self.selected})
        process = subprocess.run([str(ROOT / "ai2research"), "export_delivery", payload], capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout)
        self.assertEqual(response["status"], "incomplete")
        self.assertIsNone(response["archive"])


if __name__ == "__main__":
    unittest.main()
