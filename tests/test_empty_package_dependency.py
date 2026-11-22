"""Preserve legitimate empty Python package markers without accepting empty research outputs."""
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import publication, workspace


class EmptyPackageDependencyTests(unittest.TestCase):
    def test_only_empty_package_marker_dependency_can_be_registered_and_exported(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            project = "empty-package-fixture"
            workspace.start_project(project, ["isolated packaging fixture"])
            root = workspace.project_root(project)
            marker = root / "vendor/package/__init__.py"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"")
            artifact = workspace.register_artifact(project, "vendor/package/__init__.py", "source_dependency")
            self.assertEqual(artifact["bytes"], 0)
            self.assertEqual(artifact["sha256"], hashlib.sha256(b"").hexdigest())
            self.assertNotIn("source_dependency", publication.CODE_KINDS)
            result = publication.export_delivery(project, allow_incomplete=True)
            self.assertEqual(result["status"], "incomplete")
            import zipfile
            with zipfile.ZipFile(result["archive"]) as bundle:
                self.assertEqual(bundle.read("vendor/package/__init__.py"), b"")
            self.assertEqual(marker.read_bytes(), b"")

    def test_other_empty_dependencies_and_empty_scientific_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            project = "empty-output-fixture"
            workspace.start_project(project, ["isolated validation fixture"])
            root = workspace.project_root(project)
            for name, kind in (("dependency.py", "source_dependency"), ("chapter.tex", "manuscript_chapter"),
                               ("method.py", "method_code"), ("report.md", "report"),
                               ("__init__.py", "source_code"), ("__init__.py", "method_code")):
                with self.subTest(name=name, kind=kind):
                    (root / name).write_bytes(b"")
                    with self.assertRaisesRegex(ValueError, "Empty files"):
                        workspace.register_artifact(project, name, kind)


if __name__ == "__main__":
    unittest.main()
