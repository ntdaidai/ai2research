import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest
import zipfile
from unittest.mock import patch

from tools import bootstrap_workspace, configure_harness_mcp, install_local_skill, package_ai2research

ROOT = Path(__file__).resolve().parents[1]


class MigrationTest(unittest.TestCase):
    def test_bootstrap_keeps_python_out_of_global_bin_and_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                if argv[1] == 'venv':
                    python = bootstrap_workspace.environment_python(root / '.venv')
                    python.parent.mkdir(parents=True)
                    python.touch()
            with patch.object(bootstrap_workspace.subprocess, 'run', side_effect=run), \
                 patch.object(bootstrap_workspace.subprocess, 'check_output', return_value='Python 3.14.4\n'):
                bootstrap_workspace.bootstrap(root, 'uv-fixture', workbench_only=True)
            self.assertEqual(calls[0], ['uv-fixture', 'python', 'install', '--no-bin', '--no-registry', '3.14.4'])
            self.assertTrue(all('RESEARCH_REVIEW_API_KEY' not in arg for argv in calls for arg in argv))

    def test_default_review_budget_blocks_before_reading_credentials(self):
        from tools.harness import service
        with patch.object(service, '_api_key') as credential:
            with self.assertRaisesRegex(ValueError, 'No external review budget'):
                service.review_topic('installation fixture, no external call')
            credential.assert_not_called()

    def test_windows_paths_and_environment_variable_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "同学的目录 with spaces"
            root.mkdir()
            (root / 'harness.toml').write_text('[review]\nkey_env="MY_REVIEW_KEY"\n', encoding='utf-8')
            entry = configure_harness_mcp.configuration(root, windows=True)
            self.assertEqual(Path(entry['command']), root / '.venv/Scripts/python.exe')
            self.assertEqual(entry['args'], [str(root / 'tools/harness_mcp.py')])
            self.assertIn('MY_REVIEW_KEY', entry['env_vars'])
            self.assertEqual(bootstrap_workspace.environment_python(root, windows=True), root / 'Scripts/python.exe')

    def test_mcp_registration_preserves_settings_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'config.toml'
            original = '[mcp_servers.other]\ncommand="existing-tool"\n'
            target.write_text(original)
            configure_harness_mcp.register(ROOT, target, windows=True)
            saved = target.read_bytes()
            parsed = tomllib.loads(saved.decode())
            self.assertEqual(parsed['mcp_servers']['other']['command'], 'existing-tool')
            self.assertEqual(parsed['mcp_servers']['ai2research']['env']['PYTHONUTF8'], '1')
            configure_harness_mcp.register(ROOT, target, windows=True)
            self.assertEqual(target.read_bytes(), saved)

    def test_conflicting_mcp_entry_and_preview_never_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'config.toml'
            configure_harness_mcp.register(ROOT, target, dry_run=True)
            self.assertFalse(target.exists())
            original = b'[mcp_servers.ai2research]\ncommand="different"\n'
            target.write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'left unchanged'):
                configure_harness_mcp.register(ROOT, target)
            self.assertEqual(target.read_bytes(), original)

    def test_skill_copy_needs_no_symlinks_and_restores(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            install_local_skill.sync_skills(ROOT, home, copy=True)
            self.assertFalse(any(p.is_symlink() for p in home.rglob('*')))
            self.assertTrue((home / 'skills/ai2research/references/harness.md').is_file())
            self.assertEqual(install_local_skill.sync_skills(ROOT, home, copy=True)['operations'], [])
            install_local_skill.sync_skills(ROOT, home, restore=True)
            self.assertFalse((home / 'skills/ai2research/SKILL.md').exists())

    def test_skill_copy_preserves_an_unrelated_existing_skill(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / 'skills/ai2research/SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('user-owned guidance')
            with self.assertRaisesRegex(ValueError, 'preserved'):
                install_local_skill.sync_skills(ROOT, home, copy=True)
            self.assertEqual(path.read_text(), 'user-owned guidance')

    def test_gitless_source_package_excludes_private_state_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            for name in package_ai2research.REQUIRED:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('fixture ' + name)
            for name in ['.local/state.sqlite3', '.env', '.venv/password.py', 'research/studies/private/paper.json']:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('private sentinel')
            target = Path(temporary) / 'release.zip'
            package_ai2research.package(root, target)
            with zipfile.ZipFile(target) as archive:
                self.assertIsNone(archive.testzip())
                self.assertFalse(any('private' in n or '.env' in n or '.venv' in n for n in archive.namelist()))
                manifest = json.loads(archive.read('ai2research/RELEASE_MANIFEST.json'))
                for item in manifest['files']:
                    self.assertEqual(hashlib.sha256(archive.read('ai2research/' + item['path'])).hexdigest(), item['sha256'])
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, 'preserved'):
                package_ai2research.package(root, target)
            self.assertEqual(target.read_bytes(), before)

    def test_source_package_refuses_linked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            original = Path(temporary) / 'private.txt'
            original.write_text('do not include')
            try:
                (root / 'README.md').symlink_to(original)
            except OSError:
                self.skipTest('Symlinks are unavailable on this account')
            with self.assertRaisesRegex(ValueError, 'regular source'):
                package_ai2research.collect_sources(root)


if __name__ == '__main__':
    unittest.main()
