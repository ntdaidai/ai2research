"""The paper title comes from registered manuscript context, then project metadata."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import publication, workspace


class ManuscriptTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.project = "title-fixture"
        workspace.start_project(self.project, ["isolated paper fixture"], "计算机视觉")
        self.root = workspace.project_root(self.project)
        self.plan = publication.publication_plan(self.project)
        for name, chapter in self.plan["chapters"].items():
            lease = workspace.claim_task(self.project, chapter["task_id"], "chapter-" + name)
            text = "A completed chapter fixture with sufficient concrete source text for manuscript assembly."
            if name != "abstract":
                text = "\\section{" + name.replace("_", " ") + "}\n" + text
            (self.root / chapter["path"]).write_text(text, encoding="utf-8")
            artifact = workspace.register_artifact(self.project, chapter["path"], "manuscript_chapter")
            workspace.update_task(self.project, chapter["task_id"], lease["lease_token"], "completed", artifact_ids=[artifact["artifact_id"]])
        (self.root / "paper/references.bib").write_text("@article{fixture,title={Fixture reference},year={2020}}", encoding="utf-8")
        workspace.register_artifact(self.project, "paper/references.bib", "bibliography")

    def assemble_with_context(self, context):
        relative = self.plan["context"]
        (self.root / relative).write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        workspace.register_artifact(self.project, relative, "publication_context")
        result = publication.assemble_manuscript(self.project, compile_pdf=False)
        self.assertEqual(result["status"], "assembled")
        return next(line for line in (self.root / "paper/main.tex").read_text().splitlines() if line.startswith("\\title{"))

    def test_registered_english_paper_title_supersedes_domain_title(self):
        title = "Normal Matching-Error Calibration for One-Shot Anomaly Retrieval"
        line = self.assemble_with_context({"title": "  " + title + "  "})
        self.assertEqual(line, "\\title{" + title + "}")
        self.assertNotIn("计算机视觉", line)
        rendering = json.loads((self.root / publication.RENDER_MANIFEST).read_text())
        self.assertIn(self.plan["context"], {item["path"] for item in rendering["input_files"]})

    def test_empty_context_title_falls_back_to_project_title(self):
        for context in ({}, {"title": ""}, {"title": "   "}, {"title": None}):
            with self.subTest(context=context):
                self.assertEqual(self.assemble_with_context(context), "\\title{计算机视觉}")

    def test_context_title_cannot_introduce_tex_commands(self):
        malicious = r"Nice }\input{secret.tex}\write18{touch} % $ & # _ ^ ~"
        line = self.assemble_with_context({"title": malicious})
        content = line[len("\\title{"):-1]
        self.assertNotRegex(content, r"[\\{}%$&#_^~]")
        self.assertNotIn(r"\input", line)
        self.assertNotIn(r"\write18", line)

    def test_ieee_conference_format_and_keywords_are_built_and_bound(self):
        keywords = self.root / "paper/keywords.tex"
        keywords.write_text(r"\begin{IEEEkeywords}anomaly detection\end{IEEEkeywords}")
        self.assemble_with_context({"title": "Measured paper", "latex_format": "ieee_conference"})
        main = (self.root / "paper/main.tex").read_text()
        self.assertIn(r"\documentclass[conference]{IEEEtran}", main)
        self.assertLess(main.index(r"\RequirePackage[T1]{fontenc}"), main.index(r"\documentclass[conference]{IEEEtran}"))
        self.assertIn(r"\bibliographystyle{IEEEtran}", main)
        self.assertIn(r"\input{keywords.tex}", main)
        rendering = json.loads((self.root / publication.RENDER_MANIFEST).read_text())
        self.assertIn("paper/keywords.tex", {item["path"] for item in rendering["input_files"]})

    def test_unsupported_format_is_rejected_before_main_is_written(self):
        for value in (r"IEEEtran}\input{secret}", [], {}, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "latex_format must"):
                self.assemble_with_context({"latex_format": value})
        self.assertFalse((self.root / "paper/main.tex").exists())


if __name__ == "__main__":
    unittest.main()
