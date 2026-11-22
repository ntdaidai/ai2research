import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.harness import publication, workspace


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        workspace.start_project("paper-test", ["https://arxiv.org/abs/1706.03762"], "Measured Paper")
        self.root = workspace.project_root("paper-test")

    def artifact(self, relative, text, kind="source_code", evidence_kind="research", source_job_id=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return workspace.register_artifact("paper-test", relative, kind, evidence_kind, source_job_id)

    def complete_chapters(self):
        plan = publication.publication_plan("paper-test")
        for name, chapter in plan["chapters"].items():
            lease = workspace.claim_task("paper-test", chapter["task_id"], "chapter-" + name)
            text = "This chapter records measured evidence, reproducible settings, and carefully checked limitations."
            if name != "abstract":
                text = "\\section{" + name.replace("_", " ") + "}\n" + text
            art = self.artifact(chapter["path"], text, "manuscript_chapter")
            workspace.update_task("paper-test", chapter["task_id"], lease["lease_token"], "completed", artifact_ids=[art["artifact_id"]])
        self.artifact("paper/references.bib", "@article{sample, title={Verified Reference}, author={Author, A}, year={2020}}", "bibliography")
        return plan

    def compile(self):
        source = Path(self.tmp.name) / "fresh.pdf"
        source.write_bytes(b"%PDF-1.4\nnew build\n")
        with patch("tools.harness.service.compile_paper", return_value={"status": "completed", "pdf": str(source)}):
            return publication.assemble_manuscript("paper-test")

    def test_chapters_are_separate_idempotent_dependency_tasks(self):
        plan = publication.publication_plan("paper-test", "systems")
        self.assertEqual(plan, publication.publication_plan("paper-test", "systems"))
        self.assertEqual(len(plan["chapters"]), 8)
        abstract = plan["chapters"]["abstract"]
        with self.assertRaisesRegex(ValueError, "dependencies"):
            workspace.claim_task("paper-test", abstract["task_id"], "too-early")
        state = workspace.project_status("paper-test")
        abstract_task = next(t for t in state["tasks"] if t["task_id"] == abstract["task_id"])
        self.assertIn("systems-paper-writing", abstract_task["instructions"])
        self.assertEqual(abstract_task["output_paths"], ["paper/sections/abstract.tex"])

    def test_assembly_blocks_missing_chapters_then_preserves_separate_files(self):
        publication.publication_plan("paper-test")
        incomplete = publication.assemble_manuscript("paper-test", False)
        self.assertEqual(incomplete["status"], "incomplete")
        self.assertFalse((self.root / "paper/main.tex").exists())
        self.complete_chapters()
        result = self.compile()
        self.assertEqual(result["status"], "completed")
        text = (self.root / "paper/main.tex").read_text()
        self.assertIn("\\input{sections/results.tex}", text)
        self.assertIn("\\bibliography{references}", text)
        receipt = json.loads((self.root / "paper/build.json").read_text())
        self.assertIn("paper/sections/results.tex", receipt["inputs"])
        self.assertTrue((self.root / "paper/paper.pdf").read_bytes().startswith(b"%PDF"))

    def test_synthetic_context_is_inherited_and_visible(self):
        workspace.start_project("synthetic", ["toolchain reference"], evidence_kind="toolchain_test")
        root = workspace.project_root("synthetic")
        (root / "results.csv").write_text("label,accuracy\nbaseline,0.5\ncandidate,0.7\n")
        workspace.register_artifact("synthetic", "results.csv", "results")
        result = publication.export_results_figure("synthetic", "results.csv", "accuracy")
        self.assertEqual(result["evidence_kind"], "toolchain_test")
        script = (Path(result["directory"]) / "reproduce.py").read_text()
        self.assertIn("TOOLCHAIN TEST", script)
        self.assertTrue(all(a["evidence_kind"] == "toolchain_test" for a in result["artifacts"]))

    def test_plot_records_source_hash_and_rejects_nonfinite_or_unregistered_input(self):
        self.artifact("results.csv", "label,accuracy\nbaseline,0.5\ncandidate,0.7\n", "results")
        result = publication.export_results_figure("paper-test", "results.csv", "accuracy")
        folder = Path(result["directory"])
        self.assertTrue((folder / "figure.png").read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((folder / "figure.pdf").read_bytes().startswith(b"%PDF"))
        self.assertEqual(result["source_sha256"], hashlib.sha256((self.root / "results.csv").read_bytes()).hexdigest())
        self.artifact("bad.json", '{"rows":[{"label":"bad","accuracy":"NaN"}]}', "results")
        with self.assertRaisesRegex(ValueError, "finite"):
            publication.export_results_figure("paper-test", "bad.json", "accuracy")

    def test_delivery_labels_incomplete_and_zip_manifest_matches_bytes(self):
        self.artifact("code/method.py", "print('measured method')\n")
        blocked = publication.export_delivery("paper-test")
        self.assertEqual(blocked["status"], "incomplete")
        self.assertIsNone(blocked["archive"])
        exported = publication.export_delivery("paper-test", allow_incomplete=True)
        self.assertEqual(exported["status"], "incomplete")
        with zipfile.ZipFile(exported["archive"]) as bundle:
            manifest = json.loads(bundle.read("DELIVERY_MANIFEST.json"))
            self.assertEqual(manifest["status"], "incomplete")
            self.assertIn("WORKFLOW_RECORD.json", bundle.namelist())
            for entry in manifest["files"]:
                self.assertEqual(hashlib.sha256(bundle.read(entry["path"])).hexdigest(), entry["sha256"])
            self.assertTrue(all(not p.startswith("/") and ".." not in Path(p).parts for p in bundle.namelist()))

    def test_delivery_rejects_changed_registered_files_and_sensitive_files(self):
        self.artifact("code/method.py", "print('one')\n")
        (self.root / "code/method.py").write_text("print('changed')\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            publication.export_delivery("paper-test", True)
        workspace.register_artifact("paper-test", "code/method.py", "source_code")
        self.artifact("private.key", "unrelated secret material", "notes")
        with self.assertRaisesRegex(ValueError, "Sensitive"):
            publication.export_delivery("paper-test", True)

    def test_delivery_rejects_symlink_and_stale_build(self):
        self.complete_chapters()
        self.compile()
        chapter = self.root / "paper/sections/results.tex"
        chapter.write_text(chapter.read_text() + "\nMore measured evidence changes the manuscript.")
        workspace.register_artifact("paper-test", "paper/sections/results.tex", "manuscript_chapter")
        result = publication.export_delivery("paper-test", True)
        self.assertTrue(any("build is stale" in reason for reason in result["missing"]))
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("cannot be read through a symlink")
        (self.root / "link").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Symbolic"):
            publication.export_results_figure("paper-test", "link", "accuracy")

    def test_figure_failure_cleans_scratch_and_preserves_existing_output(self):
        art = self.artifact("results.csv", "label,accuracy\nbaseline,0.5\n", "results")
        folder = self.root / "figures" / ("accuracy-" + art["sha256"][:12])
        folder.mkdir(parents=True)
        (folder / "figure.png").write_bytes(b"existing verified output")
        failed = type("Failed", (), {"returncode": 1, "stderr": b"renderer failure"})()
        with patch.object(publication.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "rendering failed"):
                publication.export_results_figure("paper-test", "results.csv", "accuracy")
        self.assertEqual((folder / "figure.png").read_bytes(), b"existing verified output")
        self.assertEqual(list(folder.parent.glob(".figure-*")), [])

    def test_delivery_requires_latest_reviews_and_supports_split_chapter_packets(self):
        self.complete_chapters()
        for name, kind in (("code/method.py", "source_code"), ("results.json", "results"),
                           ("verification.md", "verification_results"), ("ablation.md", "ablation_results"),
                           ("REPRODUCE.md", "reproduction_readme"), ("requirements.txt", "dependency_lock"),
                           ("figures/reproduce.py", "figure_source"), ("figures/figure.png", "figure_png")):
            self.artifact(name, "Self-contained artifact for this delivery gate test.", kind)
        self.compile()
        artifacts = publication._latest_artifacts("paper-test")
        experiment = {a["artifact_id"] for a in artifacts.values() if a["kind"] in publication.CODE_KINDS | publication.RESULT_KINDS | publication.CONFIRMATION_KINDS}
        chapters = {a["artifact_id"] for p, a in artifacts.items() if p.endswith((".tex", ".bib")) or p == publication.RENDER_MANIFEST}
        packets = [{"packet_id": "e", "stage": "experiment", "artifact_ids": list(experiment)}]
        packets += [{"packet_id": "m" + str(i), "stage": "manuscript", "artifact_ids": [identifier]} for i, identifier in enumerate(chapters)]
        def validated(_project_id, artifact_id):
            artifact = next(a for a in artifacts.values() if a["artifact_id"] == artifact_id)
            job_id = {"results": "search-job", "verification_results": "verification-job", "ablation_results": "ablation-job"}.get(artifact["kind"], "other-job")
            return {"valid": True, "job_id": job_id, "job_ids": [job_id], "issues": []}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated), patch("tools.harness.evidence.list_review_packets", return_value=packets), patch("tools.harness.evidence.review_status", return_value={"state": "ready"}):
            self.assertEqual(publication._delivery_readiness("paper-test", artifacts), [])
        objection = {"packet_id": "new-objection", "stage": "manuscript", "artifact_ids": [next(iter(chapters))]}
        def verdict(_project_id, packet_id):
            return {"state": "revise" if packet_id == "new-objection" else "ready"}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated), patch("tools.harness.evidence.list_review_packets", return_value=[objection, *packets]), patch("tools.harness.evidence.review_status", side_effect=verdict):
            self.assertTrue(any("not ready" in item for item in publication._delivery_readiness("paper-test", artifacts)))

    def test_result_kind_labels_and_unexecuted_job_ids_do_not_establish_confirmation(self):
        self.artifact("results.json", '{"accuracy": 0.9}', "results", source_job_id="unexecuted-search")
        self.artifact("verification.md", "The method was independently verified.", "verification_results")
        self.artifact("ablation.md", "The ablation was completed.", "ablation_results", source_job_id="unexecuted-ablation")
        missing = publication._result_readiness("paper-test", publication._latest_artifacts("paper-test"))
        self.assertTrue(any("main results backed" in item for item in missing))
        self.assertTrue(any("independent verification requires" in item for item in missing))
        self.assertTrue(any("ablation requires" in item for item in missing))

    def test_confirmation_cannot_relabel_search_job_or_reuse_one_job_for_two_roles(self):
        for relative, kind in (("results.json", "results"), ("verification.json", "verification_results"), ("ablation.json", "ablation_results")):
            self.artifact(relative, '{"accuracy": 0.9}', kind)
        artifacts = publication._latest_artifacts("paper-test")
        def validated_with_job(job_for_kind):
            def validate(_project_id, artifact_id):
                artifact = next(a for a in artifacts.values() if a["artifact_id"] == artifact_id)
                job = job_for_kind[artifact["kind"]]
                return {"valid": True, "job_id": job, "job_ids": [job], "issues": []}
            return validate
        same_search = {"results": "search", "verification_results": "search", "ablation_results": "ablation"}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated_with_job(same_search)):
            self.assertTrue(any("independent verification requires" in item for item in publication._result_readiness("paper-test", artifacts)))
        shared_check = {"results": "search", "verification_results": "confirmation", "ablation_results": "confirmation"}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated_with_job(shared_check)):
            self.assertIn("verification and ablation cannot reuse the same executed job", publication._result_readiness("paper-test", artifacts))
        separate = {"results": "search", "verification_results": "confirmation", "ablation_results": "ablation"}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated_with_job(separate)):
            self.assertEqual(publication._result_readiness("paper-test", artifacts), [])
        from tools.harness.state import record
        record("job_evidence_attached", {"job_id": "confirmation", "phase": "search", "artifact_ids": []}, "paper-test")
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=validated_with_job(separate)):
            self.assertTrue(any("independent verification requires" in item for item in publication._result_readiness("paper-test", artifacts)))

    def test_relabeling_a_historical_search_result_cannot_make_it_independent(self):
        self.artifact("search.json", '{"metrics":{"accuracy":0.9}}', "experiment_result", source_job_id="old-search")
        self.artifact("search.json", '{"metrics":{"accuracy":0.9}}', "verification_results", source_job_id="old-search")
        self.artifact("main.json", '{"metrics":{"accuracy":0.9}}', "experiment_result", source_job_id="current-search")
        self.artifact("ablation.json", '{"metrics":{"accuracy":0.8}}', "ablation_results", source_job_id="ablation")
        artifacts = publication._latest_artifacts("paper-test")
        def verified(_project_id, artifact_id):
            artifact = next(a for a in artifacts.values() if a["artifact_id"] == artifact_id)
            return {"valid": True, "job_id": artifact["source_job_id"], "job_ids": [artifact["source_job_id"]], "issues": []}
        with patch("tools.harness.evidence.validate_result_artifact", side_effect=verified):
            missing = publication._result_readiness("paper-test", artifacts)
        self.assertTrue(any("independent verification requires" in item for item in missing))

    def test_replacing_figure_or_local_style_requires_manuscript_recompilation(self):
        self.complete_chapters()
        self.artifact("figures/architecture.pdf", "%PDF-1.4\nfirst diagram rendering", "diagram_pdf")
        style = self.root / "paper/local.sty"
        style.write_text("\\ProvidesPackage{local}\n% first style\n")
        self.compile()
        receipt = json.loads((self.root / "paper/build.json").read_text())
        self.assertIn("figures/architecture.pdf", receipt["inputs"])
        self.assertIn("paper/local.sty", receipt["inputs"])
        self.assertIn("paper/local.sty", publication._latest_artifacts("paper-test"))
        self.artifact("figures/architecture.pdf", "%PDF-1.4\nchanged diagram at the same path", "diagram_pdf")
        stale = publication.export_delivery("paper-test", allow_incomplete=True)
        self.assertIn("manuscript build is stale: figures/architecture.pdf", stale["missing"])
        self.compile()
        rebuilt = publication.export_delivery("paper-test", allow_incomplete=True)
        self.assertFalse(any("build is stale" in item for item in rebuilt["missing"]))
        style.write_text("\\ProvidesPackage{local}\n% changed style\n")
        workspace.register_artifact("paper-test", "paper/local.sty", "manuscript_style")
        stale_style = publication.export_delivery("paper-test", allow_incomplete=True)
        self.assertIn("manuscript build is stale: paper/local.sty", stale_style["missing"])

    def test_extra_tex_and_explicit_graphics_dependencies_are_tracked(self):
        self.complete_chapters()
        supplement = self.root / "paper/supplement.tex"
        supplement.write_text("Additional measured analysis in a separately included file.\n\\input{../shared.tex}\n\\bibliography{../extra-references}\n")
        shared = self.root / "shared.tex"
        shared.write_text("Root-level shared analysis explicitly included from the paper working directory.")
        extra_bib = self.root / "extra-references.bib"
        extra_bib.write_text("@article{extra,title={Additional Verified Source},author={Author, A},year={2021}}")
        image = self.root / "assets/architecture.pdf"
        image.parent.mkdir()
        image.write_bytes(b"%PDF-1.4\nexplicit project-local graphic\n")
        results = self.root / "paper/sections/results.tex"
        results.write_text(results.read_text() + "\n\\input{supplement}\n\\includegraphics{../assets/architecture.pdf}\n")
        workspace.register_artifact("paper-test", "paper/sections/results.tex", "manuscript_chapter")
        build = self.root / "paper/build"
        build.mkdir()
        (build / "generated.tex").write_text("Temporary compiler output, not manuscript source.")
        self.compile()
        receipt = json.loads((self.root / "paper/build.json").read_text())
        self.assertIn("paper/supplement.tex", receipt["inputs"])
        self.assertIn("shared.tex", receipt["inputs"])
        self.assertIn("extra-references.bib", receipt["inputs"])
        self.assertIn("assets/architecture.pdf", receipt["inputs"])
        self.assertNotIn("paper/build/generated.tex", receipt["inputs"])
        self.assertIn("paper/supplement.tex", publication._latest_artifacts("paper-test"))
        self.assertIn("shared.tex", publication._latest_artifacts("paper-test"))
        supplement.write_text("Revised measurements in the separately included file.")
        workspace.register_artifact("paper-test", "paper/supplement.tex", "manuscript_dependency")
        shared.write_text("Revised root-level shared measurement explanation.")
        workspace.register_artifact("paper-test", "shared.tex", "manuscript_dependency")
        stale = publication.export_delivery("paper-test", allow_incomplete=True)
        self.assertIn("manuscript build is stale: paper/supplement.tex", stale["missing"])
        self.assertIn("manuscript build is stale: shared.tex", stale["missing"])
        escaped = Path(self.tmp.name) / "outside.pdf"
        escaped.write_bytes(b"%PDF-1.4\noutside project\n")
        (self.root / "assets/linked.pdf").symlink_to(escaped)
        results.write_text(results.read_text() + "\n\\includegraphics{../assets/linked.pdf}\n")
        with self.assertRaisesRegex(ValueError, "Symbolic"):
            publication._build_input_paths(self.root, json.loads((self.root / "paper/plan.json").read_text()), publication._latest_artifacts("paper-test"))

    def test_recompiled_changed_figure_and_style_invalidate_real_frozen_manuscript_reviews(self):
        from tools.harness import evidence, service
        from tools.harness.state import config, record
        self.complete_chapters()
        self.artifact("figures/architecture.pdf", "%PDF-1.4\nfirst actual figure version", "diagram_pdf")
        self.artifact("paper/local.sty", "\\ProvidesPackage{local}\n% original local style\n", "manuscript_style")
        first = self.compile()
        artifacts = publication._latest_artifacts("paper-test")
        reviewed = [a["artifact_id"] for p, a in artifacts.items() if Path(p).suffix in publication.LOCAL_TEX_SUFFIXES or p == publication.RENDER_MANIFEST]
        packet = evidence.prepare_review("paper-test", "manuscript", reviewed)
        review = {"score": 8, "confidence": 0.8, "blockers": [], "issues": [], "evidence_references": [first["render_manifest"]["artifact_id"]]}
        evidence.record_review("paper-test", packet["packet_id"], "agent", review, "test-agent", "test-provider")
        cfg = config()["review"]
        def external(*_args):
            return record("review_result", {"request_id": "publication-fixture-" + review["evidence_references"][0], "provider": cfg["provider"],
                          "requested_model": cfg["model"], "actual_model": cfg["model"],
                          "finish_reason": "stop", "response": json.dumps(review)}, "paper-test")
        with patch.object(service, "review_topic", side_effect=external):
            self.assertEqual(evidence.run_external_review("paper-test", packet["packet_id"], 512)["state"], "completed")
        self.assertEqual(evidence.review_status("paper-test", packet["packet_id"])["state"], "ready")
        before = publication._delivery_readiness("paper-test", publication._latest_artifacts("paper-test"))
        self.assertFalse(any(item.startswith("manuscript") and "review" in item for item in before))
        self.artifact("figures/architecture.pdf", "%PDF-1.4\nsecond figure version with changed labels", "diagram_pdf")
        second = self.compile()
        self.assertNotEqual(first["render_manifest"]["artifact_id"], second["render_manifest"]["artifact_id"])
        self.assertEqual(evidence.review_status("paper-test", packet["packet_id"])["state"], "revise")
        after = publication._delivery_readiness("paper-test", publication._latest_artifacts("paper-test"))
        self.assertFalse(any("build is stale" in item for item in after))
        self.assertIn("manuscript review does not cover current source/results", after)
        # Freeze and pass a fresh real packet before changing the stylesheet,
        # so this assertion cannot merely inherit the earlier figure failure.
        artifacts = publication._latest_artifacts("paper-test")
        frozen_style_id = artifacts["paper/local.sty"]["artifact_id"]
        reviewed = [a["artifact_id"] for p, a in artifacts.items() if Path(p).suffix in publication.LOCAL_TEX_SUFFIXES or p == publication.RENDER_MANIFEST]
        style_packet = evidence.prepare_review("paper-test", "manuscript", reviewed)
        review = {**review, "evidence_references": [second["render_manifest"]["artifact_id"]]}
        evidence.record_review("paper-test", style_packet["packet_id"], "agent", review, "test-agent", "test-provider")
        with patch.object(service, "review_topic", side_effect=external):
            self.assertEqual(evidence.run_external_review("paper-test", style_packet["packet_id"], 512)["state"], "completed")
        self.assertEqual(evidence.review_status("paper-test", style_packet["packet_id"])["state"], "ready")
        before_style = publication._delivery_readiness("paper-test", publication._latest_artifacts("paper-test"))
        self.assertFalse(any(item.startswith("manuscript") and "review" in item for item in before_style))
        self.artifact("paper/local.sty", "\\ProvidesPackage{local}\n% revised font and label style\n", "manuscript_style")
        third = self.compile()
        self.assertNotEqual(second["render_manifest"]["artifact_id"], third["render_manifest"]["artifact_id"])
        stale_review = evidence.review_status("paper-test", style_packet["packet_id"])
        self.assertEqual(stale_review["state"], "revise")
        self.assertIn(frozen_style_id, stale_review["stale_artifact_ids"])
        after_style = publication._delivery_readiness("paper-test", publication._latest_artifacts("paper-test"))
        self.assertFalse(any("build is stale" in item for item in after_style))
        self.assertIn("manuscript review does not cover current source/results", after_style)


if __name__ == "__main__":
    unittest.main()
