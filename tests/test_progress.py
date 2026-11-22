import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tools.harness import showcase, state, workspace
from tools.harness.progress import Files, finalized_delivery


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        workspace.start_project("study", ["paper"], "研究", "research")
        self.root = Path(self.tmp.name) / "projects/study"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def artifact(self, path, kind, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return workspace.register_artifact("study", path, kind, "research")

    def task(self, identifier, role, artifact=None, status="completed"):
        payload = {"task_id": identifier, "role": role, "state": status,
                   "output_paths": [artifact["path"]] if artifact else [],
                   "artifact_ids": [artifact["artifact_id"]] if artifact else []}
        with workspace._db() as db:
            db.execute("INSERT INTO workflow_tasks VALUES (?,?,?,?)", (identifier, "study", identifier, json.dumps(payload)))

    def stages(self):
        return {s["id"]: s for s in showcase.snapshot("study")["project"]["progress"]["stages"]}

    def test_intake_does_not_advance_other_stages_and_pause_preserves_progress(self):
        stages = self.stages()
        self.assertEqual(len(stages), 12)
        self.assertEqual(stages["intake"]["state"], "completed")
        self.assertTrue(all(s["state"] != "completed" for k,s in stages.items() if k != "intake"))
        state.record("project_execution_control", {"execution_state": "paused"}, "study")
        result = showcase.snapshot("study")["project"]["progress"]
        self.assertTrue(result["paused"])
        self.assertEqual(result["completed_stages"], 1)
        self.assertIn("等待明确恢复", result["next_action"])

    def test_reading_handoffs_not_shortlist_metadata_determine_paper_and_code_progress(self):
        papers = [{"title": "A", "paper_url": "https://example.com/a", "code_url": "https://github.com/a/b",
                   "repository_commit": "a" * 40, "license": "MIT", "code_status": "verified"}]
        self.artifact("discovery/papers.json", "paper_shortlist", {"papers": papers})
        self.assertNotEqual(self.stages()["papers"]["state"], "completed")
        self.assertNotEqual(self.stages()["code"]["state"], "completed")
        report = self.artifact("candidates/read.md", "candidate_screening", b"Actual paper/code reading report")
        self.task("reading", "screening", report)
        self.assertEqual(self.stages()["papers"]["state"], "completed")
        self.assertEqual(self.stages()["code"]["state"], "completed")
        (self.root / "candidates/read.md").write_text("changed after handoff")
        self.assertNotEqual(self.stages()["papers"]["state"], "completed")
        self.assertNotEqual(self.stages()["code"]["state"], "completed")

    def test_real_runs_show_earlier_handoff_gap_without_rewriting_tasks_or_using_budget_as_target(self):
        self.task("prepare", "implementation", status="pending")
        state.record("experiment_proposal", {"title": "Frozen plan"}, "study")
        state.record("experiment_approval", {"max_runs": 64}, "study")
        state.record("job_submitted", {"job_id": "one", "state": "submitted"}, "study")
        state.record("job_collected", {"job_id": "one", "status": {"state": "completed"}, "metrics": {"score": .8}}, "study")
        before = Path(self.tmp.name, "state.sqlite3").read_bytes()
        stages = self.stages()
        self.assertEqual(stages["preparation"]["state"], "needs_check")
        self.assertEqual(stages["experiments"]["state"], "completed")
        self.assertIn("1 次提交", stages["experiments"]["summary"])
        self.assertNotIn("64", stages["experiments"]["summary"])
        self.assertEqual(Path(self.tmp.name, "state.sqlite3").read_bytes(), before)
        with showcase.read_database(Path(self.tmp.name)) as db:
            payload = db.execute("SELECT payload FROM workflow_tasks WHERE task_id='prepare'").fetchone()[0]
            self.assertEqual(json.loads(payload)["state"], "pending")

    def review(self, packet, major=False):
        with state.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS packet_reviews (packet TEXT,reviewer TEXT,project TEXT,payload TEXT)")
            for role in ("agent", "deepseek"):
                value = {"review": {"score": 9.5, "issues": [{"severity": "major"}] if major else [], "blockers": []}}
                db.execute("INSERT INTO packet_reviews VALUES (?,?,?,?)", (packet,role,"study",json.dumps(value)))
        state.record("review_packet", {"packet_id":packet,"stage":"experiment"}, "study")

    def test_high_scores_do_not_clear_major_issues_or_inherit_old_reviews(self):
        self.review("first", major=True)
        result = showcase.snapshot("study")["project"]["progress"]
        self.assertEqual(self.stages()["experiment_review"]["state"], "revision")
        self.assertEqual(result["focus_stage"], "experiment_review")
        self.review("second")
        # Both opinions are positive, but this is not a current formal acceptance receipt.
        self.assertEqual(self.stages()["experiment_review"]["state"], "awaiting")
        state.record("review_packet", {"packet_id":"third","stage":"experiment"}, "study")
        stage = self.stages()["experiment_review"]
        self.assertIn("0/2", stage["summary"])
        self.assertFalse(stage["checks"][1]["done"])

    def test_pdf_and_figures_do_not_mask_stale_source_or_unfinished_chapters(self):
        source = self.artifact("paper/sections/a.tex", "manuscript_section", b"Chapter")
        self.task("chapter", "chapter_writer", source)
        pdf = self.artifact("paper/paper.pdf", "manuscript_pdf", b"%PDF-1.4 actual fixture")
        self.artifact("paper/build.json", "manuscript_build", {"inputs": {source["path"]:source["sha256"]}, "pdf_sha256":pdf["sha256"]})
        self.artifact("figures/chart.py", "figure_source", b"print('plot')")
        self.artifact("figures/chart.png", "figure_png", b"PNG fixture")
        self.assertEqual(self.stages()["manuscript"]["state"], "completed")
        self.assertEqual(self.stages()["figures"]["state"], "completed")
        (self.root / source["path"]).write_text("revised chapter")
        (self.root / "figures/chart.png").write_bytes(b"changed chart")
        self.assertEqual(self.stages()["manuscript"]["state"], "needs_check")
        self.assertNotEqual(self.stages()["figures"]["state"], "completed")

    def test_incomplete_or_unverified_export_never_completes_delivery(self):
        state.record("delivery_exported", {"archive":"old.zip", "status":"incomplete", "missing":["experiment review"]}, "study")
        stage = self.stages()["delivery"]
        self.assertEqual(stage["state"], "needs_check")
        self.assertIn("1 项未满足", stage["summary"])
        state.record("delivery_exported", {"archive":str(self.root / "missing.zip"), "archive_sha256":"a"*64,
                                           "status":"finalized", "missing":[]}, "study")
        self.assertNotEqual(self.stages()["delivery"]["state"], "completed")

    def test_finalized_gate_receipts_require_unchanged_archive_files_and_no_new_review(self):
        paper = self.artifact("paper/paper.pdf", "manuscript_pdf", b"%PDF-1.4 fixture")
        directory = self.root / "delivery"
        archive = directory / "final.zip"
        manifest = {"project_id":"study", "status":"finalized", "missing":[],
                    "created_utc":"2026-11-21T01:00:00+00:00", "files":[paper]}
        workflow = {"reviews":[{"stage":stage,"state":"ready"} for stage in ("experiment","manuscript")]}
        with zipfile.ZipFile(archive,"w") as output:
            output.writestr("DELIVERY_MANIFEST.json",json.dumps(manifest))
            output.writestr("WORKFLOW_RECORD.json",json.dumps(workflow))
        event = {"status":"finalized","missing":[],"archive":str(archive),
                 "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()}
        def verify(reviews=()):
            return finalized_delivery(Files(Path(self.tmp.name),"study",[paper]),event,reviews)
        self.assertEqual(verify(), {"experiment","manuscript"})
        self.assertEqual(verify([{"created_utc":"2026-11-21T01:01:00+00:00"}]),set())
        (self.root / paper["path"]).write_bytes(b"changed")
        self.assertEqual(verify(),set())
        (self.root / paper["path"]).write_bytes(b"%PDF-1.4 fixture")
        archive.write_bytes(b"not the reviewed delivery")
        self.assertEqual(verify(),set())

    def test_large_delivery_uses_streaming_checks_without_exhausting_document_budget(self):
        paper = self.artifact("paper/paper.pdf", "manuscript_pdf", b"%PDF-1.4 fixture")
        archive = self.root / "delivery/large.zip"
        manifest = {"project_id":"study", "status":"finalized", "missing":[],
                    "created_utc":"2026-11-21T01:00:00+00:00", "files":[paper]}
        with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_STORED) as output:
            output.writestr("DELIVERY_MANIFEST.json",json.dumps(manifest))
            output.writestr("WORKFLOW_RECORD.json",json.dumps({"reviews":[{"stage":"experiment","state":"ready"}]}))
            output.writestr("large-fixture",b"x" * (9 * 1024 * 1024))
        event = {"status":"finalized","missing":[],"archive":str(archive),
                 "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()}
        files = Files(Path(self.tmp.name),"study",[paper])
        files.remaining = 0
        with patch.object(Path,"read_bytes",side_effect=AssertionError("large delivery must stream")):
            self.assertEqual(finalized_delivery(files,event,[]),{"experiment"})
        self.assertEqual(files.remaining,0)

    def test_all_failed_trials_need_attention_instead_of_claiming_runs_are_underway(self):
        state.record("job_submitted", {"job_id":"failed", "state":"submitted"}, "study")
        state.record("job_status", {"job_id":"failed", "state":"failed"}, "study")
        stage = self.stages()["experiments"]
        self.assertEqual(stage["state"], "needs_check")
        self.assertIn("排查失败", stage["next_action"])

    def test_complete_recorded_workflow_can_finish_and_new_review_reopens_acceptance(self):
        # A small, explicitly synthetic record fixture; no model calls or research jobs.
        artifacts = []
        def add(path,kind,value):
            result = self.artifact(path,kind,value)
            artifacts.append(result)
            return result
        add("discovery/papers.json","paper_shortlist",{"papers":[{"title":"Fixture paper","code_url":"https://github.com/a/b",
            "repository_commit":"a"*40,"license":"MIT","code_status":"verified"}]})
        reading = add("candidates/reading.md","candidate_screening",b"Synthetic reading handoff")
        self.task("screen","screening",reading)
        state.record("workflow_selection",{"task_id":"screen","reason":"Recorded fixture selection"},"study")
        for role in ("method_designer","implementation","evaluation","verification"):
            source = add(f"research/{role}.md","method_code",b"Synthetic task output")
            self.task(role,role,source)
        state.record("experiment_proposal",{"purpose":"research"},"study")
        state.record("experiment_approval",{"max_runs":3},"study")
        for phase in ("baseline","verification","ablation"):
            state.record("job_submitted",{"job_id":phase,"state":"submitted"},"study")
            state.record("job_collected",{"job_id":phase,"status":{"state":"completed"},"metrics":{"value":1}},"study")
            state.record("job_evidence_attached",{"job_id":phase,"phase":phase},"study")
        chapter = add("paper/sections/a.tex","manuscript_section",b"Synthetic chapter")
        self.task("chapter","chapter_writer",chapter)
        pdf = add("paper/paper.pdf","manuscript_pdf",b"%PDF-1.4 fixture")
        add("paper/build.json","manuscript_build",{"inputs":{chapter["path"]:chapter["sha256"]},"pdf_sha256":pdf["sha256"]})
        add("figures/plot.py","figure_source",b"Synthetic plot script")
        add("figures/plot.png","figure_png",b"Synthetic PNG")
        self.review("exp")
        with state.connect() as db:
            rows = db.execute("SELECT reviewer,payload FROM packet_reviews WHERE packet='exp'").fetchall()
            for row in rows:
                db.execute("INSERT INTO packet_reviews VALUES (?,?,?,?)",("paper",row["reviewer"],"study",row["payload"]))
        state.record("review_packet",{"packet_id":"paper","stage":"manuscript"},"study")
        archive = self.root / "delivery/final.zip"
        manifest = {"status":"finalized","missing":[],"project_id":"study","created_utc":state.now(),"files":artifacts}
        with zipfile.ZipFile(archive,"w") as output:
            output.writestr("DELIVERY_MANIFEST.json",json.dumps(manifest))
            output.writestr("WORKFLOW_RECORD.json",json.dumps({"reviews":[{"stage":s,"state":"ready"} for s in ("experiment","manuscript")]}))
        state.record("delivery_exported",{"status":"finalized","missing":[],"archive":str(archive),
            "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()},"study")
        progress = showcase.snapshot("study")["project"]["progress"]
        self.assertEqual(progress["completed_stages"],12)
        self.assertEqual(progress["focus_stage"],"delivery")
        state.record("review_packet",{"packet_id":"new","stage":"experiment"},"study")
        reopened = showcase.snapshot("study")["project"]["progress"]
        self.assertLess(reopened["completed_stages"],12)
        self.assertNotEqual(self.stages()["delivery"]["state"],"completed")


if __name__ == "__main__":
    unittest.main()
