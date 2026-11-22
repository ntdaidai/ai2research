import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.harness.showcase_catalog import public_url, research_references, tool_inventory


class ShowcaseCatalogTest(unittest.TestCase):
    def test_inventory_distinguishes_files_configuration_and_account_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex = root / "codex"
            skill = codex / "skills/ai2research/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Research skill")
            plugin = codex / "plugins/cache/openai-curated-remote/sites/1/skills/sites/SKILL.md"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("Site skill")
            (codex / "config.toml").write_text('[mcp_servers.ai2research]\ncommand="python"\n[mcp_servers.ai2research.env]\nAPI_KEY="secret-marker"\n')
            settings = {"review":{"model":"review-model","api_key":"private-marker"}}
            with patch("tools.harness.showcase_catalog.shutil.which", return_value=None):
                result = tool_inventory(root=root, home=root, codex_home=codex, settings=settings)
            items = {item["id"]:item for item in result["items"]}
            self.assertEqual(items["ai2research"]["status"], "已检测")
            self.assertEqual(items["ai2research-mcp"]["status"], "已登记")
            self.assertEqual(items["sites"]["status"], "技能包已检测")
            self.assertEqual(items["external-review"]["status"], "已配置")
            self.assertNotIn("已连接", {item["status"] for item in result["items"]})
            serialized = json.dumps(result,ensure_ascii=False)
            for private in ("secret-marker","private-marker",str(root)):
                self.assertNotIn(private,serialized)

    def test_missing_or_invalid_client_configuration_does_not_initialize_or_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "not-created"
            result = tool_inventory(root=root,home=root,codex_home=missing,settings={})
            self.assertFalse(missing.exists())
            self.assertEqual(next(t for t in result["items"] if t["id"]=="ai2research-mcp")["status"],"未检测到")
            (root / "config.toml").write_text("invalid [")
            self.assertTrue(tool_inventory(root=root,home=root,codex_home=root,settings={})["items"])

    def test_reference_links_use_registered_bytes_and_safe_public_addresses(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "projects/study/discovery/papers.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"papers":[{"title":"Paper","paper_url":"https://arxiv.org/abs/2501.01234",
                "code_url":"https://github.com/author/repo.git","repository_commit":"a"*40,"license":"MIT",
                "official_venue_url":"https://openreview.net/forum?id=paper","venue":"ICLR"}]}))
            artifact = {"path":"discovery/papers.json","sha256":hashlib.sha256(target.read_bytes()).hexdigest()}
            result = research_references(base,"study",{},[artifact])
            self.assertEqual(result["items"][0]["pdf_url"],"https://arxiv.org/pdf/2501.01234")
            self.assertEqual(result["items"][0]["code_url"],"https://github.com/author/repo")
            target.write_text('{"papers":[]}')
            self.assertEqual(research_references(base,"study",{},[artifact])["state"],"needs_check")

    def test_seed_links_and_non_public_values_are_handled_without_exposing_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            seeds={"seed_papers":["https://example.org/paper.pdf","/home/person/private.pdf","javascript:alert(1)"]}
            result=research_references(Path(directory),"study",seeds,[])
            self.assertEqual(len(result["items"]),1)
            self.assertEqual(result["items"][0]["pdf_url"],"https://example.org/paper.pdf")
        for value in ("javascript:alert(1)","file:///private.pdf","https://user:secret@example.org/paper",None):
            self.assertEqual(public_url(value),"")


if __name__ == "__main__":
    unittest.main()
