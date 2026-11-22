import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.harness import collection


class CollectionLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.local = patch.object(collection, "local_dir", return_value=self.root)
        self.local.start()
        self.job_id = "a" * 32
        self.folder = self.root / "jobs" / self.job_id

    def tearDown(self):
        self.local.stop()
        self.scratch.cleanup()

    def existing_collection(self):
        self.folder.mkdir(parents=True)
        (self.folder / "script.py").write_bytes(b"original evidence")

    def assert_no_stage(self):
        self.assertEqual(list((self.root / ".collections").iterdir()), [])

    def abandoned_stage(self, name="collection-abcdefgh", **overrides):
        stage = self.root / ".collections" / name
        stage.mkdir(parents=True)
        (stage / "partial.txt").write_bytes(b"partial output")
        owner = stage.with_name(stage.name + ".owner.json")
        data = {"pid": 123456, "hostname": socket.gethostname(), "created_unix": time.time() - 172800}
        data.update(overrides)
        owner.write_text(json.dumps(data), encoding="utf-8")
        return stage, owner

    def test_success_publishes_nested_files_and_removes_stage(self):
        result = collection.publish_collection(self.job_id, {"nested/result.json": b"{}"})
        self.assertEqual(result, self.folder)
        self.assertEqual((result / "nested/result.json").read_bytes(), b"{}")
        self.assert_no_stage()

    def test_success_preserves_previous_evidence_in_backup(self):
        self.existing_collection()
        collection.publish_collection(self.job_id, {"script.py": b"new evidence"})
        backups = list((self.root / ".collection-recovery").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "script.py").read_bytes(), b"original evidence")
        self.assertEqual((self.folder / "script.py").read_bytes(), b"new evidence")
        self.assert_no_stage()

    def test_stage_has_owner_until_publication_finishes(self):
        write = Path.write_bytes

        def inspect_owner(path, data):
            owners = list((self.root / ".collections").glob("*.owner.json"))
            self.assertEqual(len(owners), 1)
            metadata = json.loads(owners[0].read_text())
            self.assertEqual(metadata["pid"], os.getpid())
            self.assertEqual(metadata["hostname"], socket.gethostname())
            self.assertLess(abs(metadata["created_unix"] - time.time()), 5)
            return write(path, data)

        with patch.object(Path, "write_bytes", inspect_owner):
            collection.publish_collection(self.job_id, {"script.py": b"evidence"})
        self.assert_no_stage()

    def test_write_failure_cleans_partial_stage_and_preserves_canonical(self):
        self.existing_collection()
        write = Path.write_bytes

        def fail_second(path, data):
            if path.name == "second.txt":
                raise OSError("simulated disk full")
            return write(path, data)

        with patch.object(Path, "write_bytes", fail_second):
            with self.assertRaisesRegex(OSError, "disk full"):
                collection.publish_collection(self.job_id, {"first.txt": b"partial", "second.txt": b"failed"})
        self.assertEqual((self.folder / "script.py").read_bytes(), b"original evidence")
        self.assert_no_stage()

    def test_first_publication_failure_cleans_stage(self):
        with patch.object(Path, "rename", side_effect=OSError("simulated rename failure")):
            with self.assertRaisesRegex(OSError, "rename failure"):
                collection.publish_collection(self.job_id, {"script.py": b"new evidence"})
        self.assertFalse(self.folder.exists())
        self.assert_no_stage()

    def test_failed_replacement_restores_previous_collection(self):
        self.existing_collection()
        rename = Path.rename

        def fail_publication(path, target):
            if path.parent == self.root / ".collections":
                raise OSError("simulated publication failure")
            return rename(path, target)

        with patch.object(Path, "rename", fail_publication):
            with self.assertRaisesRegex(OSError, "publication failure"):
                collection.publish_collection(self.job_id, {"script.py": b"new evidence"})
        self.assertEqual((self.folder / "script.py").read_bytes(), b"original evidence")
        self.assertEqual(list((self.root / ".collection-recovery").iterdir()), [])
        self.assert_no_stage()

    def test_failed_restore_keeps_backup_and_cleans_stage(self):
        self.existing_collection()
        rename = Path.rename

        def fail_after_backup(path, target):
            if path.parent in (self.root / ".collections", self.root / ".collection-recovery"):
                raise OSError("simulated publication and restoration failure")
            return rename(path, target)

        with patch.object(Path, "rename", fail_after_backup):
            with self.assertRaisesRegex(OSError, "restoration failure"):
                collection.publish_collection(self.job_id, {"script.py": b"new evidence"})
        backups = list((self.root / ".collection-recovery").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "script.py").read_bytes(), b"original evidence")
        self.assert_no_stage()

    def test_cleanup_removes_only_registered_dead_local_stage(self):
        stage, owner = self.abandoned_stage()
        unknown = stage.parent / "old-unregistered"
        unknown.mkdir()
        backup = self.root / ".collection-recovery" / "old-evidence"
        backup.mkdir(parents=True)
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError):
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(result["removed_stages"], 1)
        self.assertEqual(result["removed_owners"], 1)
        self.assertEqual(result["failures"], {})
        self.assertFalse(stage.exists())
        self.assertFalse(owner.exists())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(backup.is_dir())
        self.assertEqual(collection.cleanup_abandoned_collections()["examined"], 0)

    @unittest.skipUnless(os.name == "posix", "SIGKILL is a POSIX lifecycle check")
    def test_cleanup_recovers_stage_after_real_process_kill(self):
        script = """import os, signal
from pathlib import Path
from tools.harness.collection import publish_collection
def crash(path, content):
    os.kill(os.getpid(), signal.SIGKILL)
Path.write_bytes = crash
publish_collection('a' * 32, {'script.py': b'fixture'})
"""
        result = subprocess.run([sys.executable, "-B", "-c", script],
                                cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, "RESEARCH_STATE_DIR": str(self.root)},
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertEqual(len(list((self.root / ".collections").glob("*.owner.json"))), 1)
        summary = collection.cleanup_abandoned_collections(min_age_seconds=0)
        self.assertEqual(summary["removed_stages"], 1)
        self.assertEqual(summary["removed_owners"], 1)
        self.assert_no_stage()

    def test_cleanup_preserves_young_foreign_and_live_owners(self):
        retained = [self.abandoned_stage("collection-youngabc", created_unix=time.time()),
                    self.abandoned_stage("collection-foreignx", hostname="another-host"),
                    self.abandoned_stage("collection-livepid_")]
        with patch.object(collection.os, "kill") as check_pid:
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(check_pid.call_count, 1)
        self.assertEqual(result["retained"], 3)
        for stage, owner in retained:
            self.assertTrue(stage.is_dir())
            self.assertTrue(owner.is_file())

    def test_cleanup_after_publication_only_removes_stale_owner(self):
        stage, owner = self.abandoned_stage()
        self.folder.parent.mkdir(parents=True)
        stage.rename(self.folder)
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError):
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(result["removed_stages"], 0)
        self.assertEqual(result["removed_owners"], 1)
        self.assertFalse(owner.exists())
        self.assertEqual((self.folder / "partial.txt").read_bytes(), b"partial output")

    def test_cleanup_rejects_symlinks_and_does_not_follow_json_paths(self):
        stage, owner = self.abandoned_stage()
        outside = self.root / "evidence"
        outside.mkdir()
        stage.rename(outside / "retained")
        stage.symlink_to(outside, target_is_directory=True)
        second, marker = self.abandoned_stage("collection-marker__")
        metadata = outside / "owner.json"
        marker.rename(metadata)
        marker.symlink_to(metadata)
        third, third_owner = self.abandoned_stage("collection-jsonpath", path=str(outside))
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError):
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(result["failures"], {"ValueError": 2})
        self.assertEqual(result["removed_stages"], 1)
        self.assertTrue(outside.is_dir())
        self.assertTrue(stage.is_symlink())
        self.assertTrue(owner.is_file())
        self.assertTrue(second.is_dir())
        self.assertTrue(marker.is_symlink())
        self.assertFalse(third.exists())
        self.assertFalse(third_owner.exists())

    def test_cleanup_is_bounded_and_retains_malformed_marker(self):
        first, marker = self.abandoned_stage()
        marker.write_text("incomplete JSON")
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError):
            result = collection.cleanup_abandoned_collections(limit=1)
        self.assertEqual(result["examined"], 1)
        self.assertEqual(result["failures"], {"JSONDecodeError": 1})
        self.assertTrue(first.is_dir())
        self.assertTrue(marker.is_file())
        marker.write_text(json.dumps({"pid": 123456, "hostname": socket.gethostname(), "created_unix": 1}))
        self.abandoned_stage("collection-second__")
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError):
            result = collection.cleanup_abandoned_collections(limit=1)
        self.assertEqual(result["examined"], 1)
        self.assertEqual(result["removed_stages"], 1)
        self.assertEqual(len(list(marker.parent.glob("*.owner.json"))), 1)

    def test_cleanup_keeps_registration_after_delete_failure(self):
        stage, owner = self.abandoned_stage()
        with patch.object(collection.os, "kill", side_effect=ProcessLookupError), \
             patch.object(collection.shutil, "rmtree", side_effect=PermissionError):
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(result["failures"], {"PermissionError": 1})
        self.assertTrue(stage.is_dir())
        self.assertTrue(owner.is_file())

    def test_cleanup_tolerates_another_collector_removing_the_same_stage(self):
        stage, owner = self.abandoned_stage()

        def concurrently_cleaned(pid, sig):
            collection.shutil.rmtree(stage)
            owner.unlink()
            raise ProcessLookupError

        with patch.object(collection.os, "kill", side_effect=concurrently_cleaned):
            result = collection.cleanup_abandoned_collections()
        self.assertEqual(result["failures"], {})
        self.assert_no_stage()

    def test_cleanup_rejects_invalid_bounds_and_symlink_root(self):
        for value in (-1, float("nan"), True):
            with self.assertRaises(ValueError):
                collection.cleanup_abandoned_collections(min_age_seconds=value)
        for value in (0, 257, True):
            with self.assertRaises(ValueError):
                collection.cleanup_abandoned_collections(limit=value)
        outside = self.root / "evidence"
        outside.mkdir()
        (self.root / ".collections").symlink_to(outside, target_is_directory=True)
        self.assertEqual(collection.cleanup_abandoned_collections()["failures"], {"UnsafeStagingDirectory": 1})
        self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
