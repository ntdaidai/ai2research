import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import install_local_skill as installer


class SkillConsolidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.home = base / "source", base / "codex"
        repo = Path(__file__).resolve().parents[1]
        shutil.copytree(repo / "skills", self.root / "skills")
        self.originals = {}
        for name in installer.ALIASES:
            folder = self.home / "skills" / name
            folder.mkdir(parents=True)
            raw = f"original guidance for {name}".encode()
            (folder / "SKILL.md").write_bytes(raw)
            (folder / "template.tex").write_text("preserve the existing template")
            self.originals[name] = raw

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        before = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        result = installer.sync_skills(self.root, self.home, dry_run=True)
        after = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        self.assertTrue(result["operations"])
        self.assertEqual(before, after)
        self.assertFalse((self.home / "skill-backups").exists())

    def test_install_is_idempotent_and_restores_originals_and_templates(self):
        installer.sync_skills(self.root, self.home)
        again = installer.sync_skills(self.root, self.home)
        self.assertEqual(again["operations"], [])
        for name in installer.ALIASES:
            self.assertIn("research-workflow", (self.home / "skills" / name / "SKILL.md").read_text())
        installer.sync_skills(self.root, self.home, restore=True)
        for name, raw in self.originals.items():
            folder = self.home / "skills" / name
            self.assertEqual((folder / "SKILL.md").read_bytes(), raw)
            self.assertEqual((folder / "template.tex").read_text(), "preserve the existing template")
            self.assertFalse((folder / "agents" / "openai.yaml").exists())
        self.assertFalse((self.home / "skills" / "research-workflow").exists())
        self.assertTrue((self.home / "skill-backups" / "research-workflow" / "restored-manifest.json").is_file())

    def test_new_managed_revision_preserves_first_original_backup(self):
        installer.sync_skills(self.root, self.home)
        name = installer.ALIASES[0]
        source = self.root / "skills" / name / "SKILL.md"
        source.write_text(source.read_text() + "\nUpdated compatibility hint\n")
        installer.sync_skills(self.root, self.home)
        self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), source.read_bytes())
        installer.sync_skills(self.root, self.home, restore=True)
        self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), self.originals[name])

    def test_user_changes_are_preserved_on_update_and_restore(self):
        installer.sync_skills(self.root, self.home)
        modified = self.home / "skills" / installer.ALIASES[0] / "SKILL.md"
        modified.write_text("user edited after consolidation")
        for restore in (False, True):
            with self.assertRaisesRegex(ValueError, "Local changes preserved"):
                installer.sync_skills(self.root, self.home, restore=restore)
        self.assertEqual(modified.read_text(), "user edited after consolidation")

    def test_interrupted_install_resumes_from_write_ahead_mapping(self):
        original_write = installer._write
        def interrupted(path, raw):
            if self.home / "skills" in path.parents:
                raise OSError("interrupted before replacing the skill")
            original_write(path, raw)
        with patch.object(installer, "_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                installer.sync_skills(self.root, self.home)
        manifest_path = self.home / "skill-backups" / "research-workflow" / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        saved = json.loads(manifest_path.read_text())
        self.assertTrue(saved["files"])
        installer.sync_skills(self.root, self.home)
        installer.sync_skills(self.root, self.home, restore=True)
        for name, raw in self.originals.items():
            self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), raw)

    def test_unrelated_link_causes_no_partial_changes(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        (self.home / "skills" / "research-workflow").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unrelated skill preserved"):
            installer.sync_skills(self.root, self.home)
        for name, raw in self.originals.items():
            self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), raw)
        self.assertFalse((self.home / "skill-backups").exists())

    def test_interrupted_upgrade_accepts_previous_managed_revision(self):
        installer.sync_skills(self.root, self.home)
        name = installer.ALIASES[0]
        source = self.root / "skills" / name / "SKILL.md"
        source.write_text(source.read_text() + "\nNew stage hint\n")
        original_write = installer._write
        def interrupted(path, raw):
            if path == self.home / "skills" / name / "SKILL.md":
                raise OSError("interrupted upgrade")
            original_write(path, raw)
        with patch.object(installer, "_write", side_effect=interrupted):
            with self.assertRaises(OSError):
                installer.sync_skills(self.root, self.home)
        installer.sync_skills(self.root, self.home)
        self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), source.read_bytes())
        installer.sync_skills(self.root, self.home, restore=True)
        self.assertEqual((self.home / "skills" / name / "SKILL.md").read_bytes(), self.originals[name])

    def test_restore_does_not_follow_a_new_user_symlink(self):
        installer.sync_skills(self.root, self.home)
        target = self.home / "skills" / installer.ALIASES[0]
        moved = Path(self.tmp.name) / "moved-user-skill"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        before = (moved / "SKILL.md").read_bytes()
        with self.assertRaisesRegex(ValueError, "Linked skill resource preserved"):
            installer.sync_skills(self.root, self.home, restore=True)
        self.assertEqual((moved / "SKILL.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
