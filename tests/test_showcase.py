import hashlib
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from tools.harness import showcase, state, workspace


class ShowcaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start()
        workspace.start_project("research", ["paper"], "研究项目", "research")
        workspace.start_project("paused", ["paper"], "暂停项目", "research")
        state.record("project_execution_control", {"execution_state": "paused"}, "paused")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def output(self, path="paper/paper.pdf", content=b"%PDF-1.4 test"):
        target = self.directory / "projects" / "research" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return workspace.register_artifact("research", path, "manuscript_pdf", "research")

    def test_snapshot_is_read_only_and_projects_remain_isolated(self):
        state.record("job_submitted", {"job_id": "one", "state": "submitted", "secret": "do-not-show"}, "research")
        state.record("job_collected", {"job_id": "one", "status": {"state": "completed"}}, "research")
        state.record("job_submitted", {"job_id": "other", "state": "submitted"}, "paused")
        self.output(content=b"%PDF-1.4 first")
        latest = self.output(content=b"%PDF-1.4 second")
        database = self.directory / "state.sqlite3"
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        with showcase.read_database(self.directory) as db:
            records = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM events")
        with patch.object(state, "connect", side_effect=AssertionError("must not open writable connection")):
            result = showcase.snapshot()
        self.assertEqual(result["project"]["id"], "research")
        self.assertEqual(result["project"]["jobs_total"], 1)
        self.assertEqual(result["project"]["job_counts"], {"completed": 1})
        self.assertEqual(result["project"]["outputs"][0]["artifact_id"], latest["artifact_id"])
        self.assertEqual(result["project"]["artifact_count"], 1)
        self.assertNotIn("do-not-show", json.dumps(result))
        self.assertNotIn(str(self.directory), json.dumps(result))
        self.assertEqual(showcase.snapshot("paused")["project"]["execution_state"], "paused")
        with showcase.read_database(self.directory) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], records)
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

    def test_empty_state_does_not_initialize_directories_or_schema(self):
        missing = self.directory / "does-not-exist"
        self.assertIsNone(showcase.snapshot(directory=missing)["project"])
        self.assertFalse(missing.exists())
        legacy = self.directory / "legacy"
        legacy.mkdir()
        with closing(sqlite3.connect(legacy / "state.sqlite3")) as db:
            db.execute("CREATE TABLE unrelated (id INTEGER)")
        self.assertEqual(showcase.snapshot(directory=legacy)["projects"], [])
        with closing(sqlite3.connect(legacy / "state.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("unrelated",)])

    def test_changed_old_cross_project_and_symlink_outputs_are_rejected(self):
        old = self.output(content=b"%PDF-1.4 first")
        latest = self.output(content=b"%PDF-1.4 second")
        with self.assertRaises(KeyError):
            showcase.artifact_bytes(self.directory, "research", old["artifact_id"])
        with self.assertRaises(KeyError):
            showcase.artifact_bytes(self.directory, "paused", latest["artifact_id"])
        content, filename, mime = showcase.artifact_bytes(self.directory, "research", latest["artifact_id"])
        self.assertEqual((content, filename, mime), (b"%PDF-1.4 second", "paper.pdf", "application/pdf"))
        target = self.directory / "projects/research/paper/paper.pdf"
        target.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            showcase.artifact_bytes(self.directory, "research", latest["artifact_id"])
        target.unlink()
        external = self.directory / "outside.pdf"
        external.write_bytes(content)
        target.symlink_to(external)
        with self.assertRaises(ValueError):
            showcase.artifact_bytes(self.directory, "research", latest["artifact_id"])

    def test_path_traversal_and_other_registered_files_are_not_outputs(self):
        artifact = self.output()
        with self.assertRaises(ValueError):
            showcase.artifact_bytes(self.directory, "../research", artifact["artifact_id"])
        target = self.directory / "projects/research/paper/private.json"
        target.write_text('{"key":"secret"}')
        private = workspace.register_artifact("research", "paper/private.json", "audit_record", "research")
        with self.assertRaises(KeyError):
            showcase.artifact_bytes(self.directory, "research", private["artifact_id"])

    def test_review_history_never_claims_current_gate_passed(self):
        with state.connect() as db:
            db.execute("CREATE TABLE packet_reviews (packet TEXT, reviewer TEXT, project TEXT, payload TEXT)")
            for role in ("agent", "deepseek"):
                payload = {"review": {"score": 9, "blockers": [], "issues": []}}
                db.execute("INSERT INTO packet_reviews VALUES (?,?,?,?)", ("packet", role, "research", json.dumps(payload)))
        state.record("review_packet", {"packet_id": "packet", "stage": "manuscript"}, "research")
        result = showcase.snapshot("research")["project"]["reviews"][0]
        self.assertEqual(result["recorded_status"], "reviews_recorded")
        self.assertNotIn("ready", result.values())
        with state.connect() as db:
            payload = {"review": {"score": 9, "blockers": [], "issues": [{"severity": "major"}]}}
            db.execute("UPDATE packet_reviews SET payload=? WHERE reviewer='deepseek'", (json.dumps(payload),))
        self.assertEqual(showcase.snapshot("research")["project"]["reviews"][0]["recorded_status"], "revision_requested")

    def test_http_serves_page_and_verified_output_but_no_project_directory(self):
        artifact = self.output()
        server = showcase.make_server(port=0, directory=self.directory)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("script-src 'self'", response.getheader("Content-Security-Policy"))
            self.assertIn("研究总览".encode(), response.read())
            connection.request("GET", "/api/snapshot?project=paused")
            self.assertEqual(json.loads(connection.getresponse().read())["project"]["execution_state"], "paused")
            connection.request("GET", "/api/artifact?" + urlencode({"project": "research", "id": artifact["artifact_id"]}))
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"%PDF-1.4 test")
            for path in ("/harness.toml", "/../harness.toml", "/.local/state.sqlite3", "/api/snapshot?project=absent"):
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 404, path)
                response.read()
            connection.request("GET", "/api/snapshot", headers={"Host": "foreign.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
