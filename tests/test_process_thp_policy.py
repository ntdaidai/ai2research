"""Opt-in process-local THP policy is frozen, inherited by exec, and never a global default."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.harness import experiments, remote_worker, service


class ProcessThpProposalTests(unittest.TestCase):
    def test_opt_in_is_frozen_and_false_keeps_legacy_resource_manifest_unchanged(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            script = Path(folder) / "method.py"
            script.write_text("# isolated proposal, never executed")
            (Path(folder) / "evaluate.py").write_text("# independent frozen fixture")
            args = dict(project_id="thp-proposal-fixture", title="fixture", script_path=str(script), gpu_memory_mb=0,
                        input_files=["evaluate.py"], evaluation_files=["evaluate.py"], purpose="toolchain_test")
            legacy = service.prepare_experiment(**args)
            explicit_false = service.prepare_experiment(**args, disable_transparent_hugepages=False)
            enabled = service.prepare_experiment(**args, disable_transparent_hugepages=True)
            self.assertEqual(legacy["manifest_sha256"], explicit_false["manifest_sha256"])
            self.assertNotIn("disable_transparent_hugepages", legacy["resources"])
            self.assertTrue(enabled["resources"]["disable_transparent_hugepages"])
            manifests = [json.loads(Path(plan["manifest_path"]).read_text()) for plan in (legacy, enabled)]
            self.assertEqual(manifests[0]["script_sha256"], manifests[1]["script_sha256"])
            self.assertEqual(manifests[0]["files"], manifests[1]["files"])
            approval = experiments.approve_proposal(legacy["proposal_id"], "Isolated test authorization", 2, True)
            incompatible = service.prepare_experiment(**args, parent_proposal_id=legacy["proposal_id"], disable_transparent_hugepages=True)
            compatible = service.prepare_experiment(**args, parent_proposal_id=legacy["proposal_id"], disable_transparent_hugepages=False)
            with patch.object(service, "_remote", return_value={"state": "mocked"}) as remote:
                with self.assertRaisesRegex(ValueError, "resources"):
                    experiments.submit_proposal(incompatible["proposal_id"], approval["event_id"])
                remote.assert_not_called()
                experiments.submit_proposal(compatible["proposal_id"], approval["event_id"])
                self.assertNotIn("disable_transparent_hugepages", remote.call_args.args[0]["spec"])
            approval_enabled = experiments.approve_proposal(enabled["proposal_id"], "Isolated true-policy fixture authorization")
            with patch.object(service, "_remote", return_value={"state": "mocked"}) as remote:
                experiments.submit_proposal(enabled["proposal_id"], approval_enabled["event_id"])
                self.assertTrue(remote.call_args.args[0]["spec"]["disable_transparent_hugepages"])

    def test_parameter_requires_actual_boolean(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RESEARCH_STATE_DIR": folder}):
            source = Path(folder) / "method.py"
            source.write_text("# never executed")
            for value in (1, 0, "true", None):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be a boolean"):
                    service.prepare_experiment("thp-type-fixture", "fixture", str(source), disable_transparent_hugepages=value)


@unittest.skipUnless(sys.platform == "linux", "Linux process-local prctl THP policy")
class ProcessThpWorkerTests(unittest.TestCase):
    def test_flag_survives_exec_and_default_preserves_inherited_semantics(self):
        inherited = ctypes.CDLL(None).prctl(42, 0, 0, 0, 0)
        parent_pid = os.getpid()
        code = "import ctypes,json,os; from tools.harness.remote_worker import child_resource_sample; print(json.dumps({'get':ctypes.CDLL(None).prctl(42,0,0,0,0),'sample':child_resource_sample(os.getpid())}))"
        for flag in (False, True):
            with self.subTest(flag=flag):
                spec = {"timeout_seconds": 10, "memory_mb": 512}
                if flag:
                    spec["disable_transparent_hugepages"] = True
                completed = subprocess.run([sys.executable, "-c", code], preexec_fn=lambda: remote_worker.child_setup(spec, parent_pid),
                                           capture_output=True, text=True, timeout=15)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                self.assertEqual(result["get"], 1 if flag else inherited)
                if "THP_enabled" in result["sample"]:
                    self.assertEqual(result["sample"]["THP_enabled"], 0 if flag or inherited == 1 else 1)
                else:
                    self.assertEqual(result["sample"]["thp_state"], "unavailable")
        self.assertEqual(ctypes.CDLL(None).prctl(42, 0, 0, 0, 0), inherited)

    def test_mandatory_policy_failure_raises_before_limits_without_fallback(self):
        libc = Mock()
        libc.prctl.side_effect = [0, -1]  # parent-death signal succeeds, requested THP policy fails
        spec = {"disable_transparent_hugepages": True}
        with patch.object(ctypes, "CDLL", return_value=libc), patch.object(remote_worker, "limits") as limits, patch.object(os, "write") as log:
            with self.assertRaisesRegex(OSError, "Cannot enforce requested process-local THP"):
                remote_worker.child_setup(spec, os.getppid())
            limits.assert_not_called()
            self.assertIn(b"no fallback", log.call_args.args[1])
        self.assertEqual(libc.prctl.call_args_list[1].args, (41, 1, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
