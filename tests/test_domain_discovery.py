import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import discovery, workspace
from tools.harness.state import ROOT


class DomainDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"RESEARCH_STATE_DIR": self.tmp.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.board = discovery.start_from_request("domain-test", "联邦学习", "单3090，低资源")

    def papers(self):
        return [{"paper_id":f"paper-{i}","title":f"Distinct federated method {i}","venue":"ICLR",
                 "official_venue_url":f"https://openreview.net/forum?id=fixture{i}","publication_date":"2026-04-01",
                 "paper_url":f"https://openreview.net/pdf?id=fixture{i}","code_url":f"https://github.com/author/project-{i}",
                 "repository_commit":str(i)*40,"license":"MIT","publication_status":"accepted","code_status":"verified",
                 "venue_evidence":"Test metadata declaration: official accepted venue entry checked by controller",
                 "code_evidence":"Test metadata declaration: author link, source and license inspected"} for i in range(5)]

    def test_domain_start_is_idempotent_has_no_invented_paper_seed(self):
        repeated = discovery.start_from_request("domain-test", "联邦学习", "单3090，低资源")
        self.assertEqual(self.board["seed_papers"], [])
        self.assertEqual(self.board["tasks"][0]["task_id"], repeated["tasks"][0]["task_id"])
        self.assertEqual(len(repeated["tasks"]), 1)
        self.assertEqual(repeated["tasks"][0]["role"], "literature_search")
        self.assertEqual(repeated["discovery"]["publication_date_from"], "2025-11-18")
        with self.assertRaises(ValueError):
            discovery.start_from_request("domain-test", "计算机视觉")

    def test_candidates_make_five_reading_tasks_without_claiming_reading_done(self):
        papers = self.papers()
        first = discovery.record_paper_candidates("domain-test", papers)
        second = discovery.record_paper_candidates("domain-test", [dict(reversed(list(p.items()))) for p in reversed(papers)])
        self.assertEqual(len(first["tasks"]), 5)
        self.assertEqual([t["task_id"] for t in first["tasks"]], [t["task_id"] for t in second["tasks"]])
        self.assertTrue(all(t["role"] == "screening" and t["state"] == "pending" for t in first["tasks"]))
        self.assertEqual(first["status"], "metadata_registered_requires_independent_reading")
        self.assertEqual(len(workspace.project_status("domain-test")["tasks"]), 6)
        self.assertTrue((workspace.project_root("domain-test") / "discovery/papers.json").is_file())

    def test_any_missing_condition_rejects_entire_shortlist_before_side_effects(self):
        variants = []
        for field in ["paper_url", "official_venue_url", "code_url", "license", "repository_commit", "venue_evidence", "code_evidence"]:
            papers = self.papers(); del papers[-1][field]; variants.append(papers)
        for field,value in [("official_venue_url","https://arxiv.org/abs/2601.00001"),("publication_status","preprint"),("code_status","coming soon"),("publication_date","2025-10-01"),
                            ("publication_date","2027-01-01"),("license","unknown"),("repository_commit","main"),("venue","arXiv")]:
            papers = self.papers(); papers[-1][field]=value; variants.append(papers)
        for papers in variants:
            with self.subTest(papers=papers[-1]):
                with self.assertRaises(ValueError):
                    discovery.record_paper_candidates("domain-test", papers)
                self.assertEqual(len(workspace.project_status("domain-test")["tasks"]),1)
                self.assertFalse((workspace.project_root("domain-test") / "discovery/papers.json").exists())

    def test_count_duplicate_and_credential_urls_rejected(self):
        for papers in [self.papers()[:4], self.papers()+[self.papers()[0]], [self.papers()[0]]*5]:
            with self.assertRaises(ValueError): discovery.record_paper_candidates("domain-test", papers)
        papers=self.papers(); papers[0]["code_url"]="https://secret@github.com/author/project"
        with self.assertRaises(ValueError): discovery.record_paper_candidates("domain-test", papers)
        papers=self.papers(); papers[-1]["code_url"]=papers[0]["code_url"]
        with self.assertRaises(ValueError): discovery.record_paper_candidates("domain-test", papers)

    def test_cross_process_domain_cli_and_old_paper_cli_both_work(self):
        def run(*args):
            r=subprocess.run([str(ROOT/'ai2research'),*args],cwd=ROOT,capture_output=True,text=True,check=True,timeout=30)
            return json.loads(r.stdout)
        a=run('start','--domain','计算机视觉','--requirements','低资源','--project','cli-domain')
        self.assertEqual(a['seed_papers'],[])
        self.assertEqual(a['tasks'][0]['role'],'literature_search')
        b=run('start','paper-a','paper-b','--project','cli-paper','--toolchain-test')
        self.assertEqual(b['seed_papers'],['paper-a','paper-b'])
        self.assertEqual(len(b['tasks']),2)

    def test_shortlist_change_requires_explicit_new_project(self):
        discovery.record_paper_candidates('domain-test',self.papers())
        papers=self.papers();papers[0]['title']='Different documented paper'
        with self.assertRaises(ValueError): discovery.record_paper_candidates('domain-test',papers)


if __name__ == '__main__': unittest.main()
