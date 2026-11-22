import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import documents, workspace


class PaperInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        workspace.start_project("input", ["local document"], evidence_kind="toolchain_test")

    def test_extraction_retains_source_and_parser_without_claiming_ocr(self):
        source = Path(self.tmp.name) / "input.pdf"
        source.write_bytes(b"%PDF-1.4 toolchain parser fixture")
        with patch.object(documents.shutil, "which", return_value="/usr/bin/pdftotext"), patch.object(
                documents.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"extracted text\n", b"")) as run:
            result = documents.extract_paper_text("input", str(source))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_artifact"]["evidence_kind"], "toolchain_test")
        self.assertEqual(result["parser"], "pdftotext -layout")
        self.assertIn("table structure", result["coverage"])
        self.assertNotIn("shell", run.call_args.kwargs)
        root = workspace.project_root("input")
        self.assertEqual((root / result["source_artifact"]["path"]).read_bytes(), source.read_bytes())

    def test_scan_without_text_requires_ocr_and_publishes_nothing(self):
        source = Path(self.tmp.name) / "scan.pdf"
        source.write_bytes(b"%PDF scan fixture")
        with patch.object(documents.shutil, "which", return_value="pdftotext"), patch.object(
                documents.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"\n\f", b"")):
            result = documents.extract_paper_text("input", str(source))
        self.assertEqual(result["status"], "needs_ocr")
        self.assertEqual(workspace.list_artifacts("input"), [])

    def test_text_inputs_are_preserved_and_failed_pdf_cannot_return_old_output(self):
        text = Path(self.tmp.name) / "paper.md"
        text.write_text("Source paragraph with a method and a limitation.")
        first = documents.extract_paper_text("input", str(text))
        self.assertEqual(first, documents.extract_paper_text("input", str(text)))
        pdf = Path(self.tmp.name) / "broken.pdf"
        pdf.write_bytes(b"%PDF broken fixture")
        with patch.object(documents.shutil, "which", return_value="pdftotext"), patch.object(
                documents.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"failure")):
            failed = documents.extract_paper_text("input", str(pdf))
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("text_artifact", failed)


if __name__ == "__main__":
    unittest.main()
