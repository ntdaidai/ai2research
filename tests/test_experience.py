import hashlib
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.harness import experience, remote_worker
from tools.harness.state import connect


class ExperienceTest(unittest.TestCase):
    def test_collection_cannot_promote_saved_failure_by_changing_response_status(self):
        from tools.harness.collection import validate_collection
        job = 'b'*32
        raw = {'spec.json': b'{"kind":"toolchain_test"}', 'script.py': b'print(1)',
               'config.json': b'{}', 'status.json': json.dumps({'job_id': job, 'state': 'failed'}).encode()}
        response = {'job_id': job, 'status': {'job_id': job, 'state': 'completed'}, 'metrics': {}, 'metrics_error': None,
                    'files': [{'path': name, 'base64': base64.b64encode(value).decode(),
                               'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)} for name, value in raw.items()]}
        with self.assertRaisesRegex(ValueError, 'status disagrees'):
            validate_collection(job, response, {})

    def test_cpu_limit_is_distinct_from_wall_and_sigkill_is_not_called_oom(self):
        cpu = experience.diagnose({'state': 'failed', 'exit_code': -signal.SIGXCPU}, {}, '')
        self.assertEqual(cpu[0]['code'], 'process_cpu_limit')
        self.assertEqual(cpu[0]['fix_status'], 'requires_controller_review')
        unknown = experience.diagnose({'state': 'failed', 'exit_code': -signal.SIGKILL}, {}, '')
        self.assertEqual(unknown[0]['code'], 'unexplained_sigkill')
        self.assertEqual(experience.diagnose({'state': 'completed'}, {}, ''), [])
        self.assertEqual(experience.diagnose({'state': 'completed'}, {}, '', 'no metrics')[0]['code'],
                         'invalid_or_missing_metrics')

    def test_evaluation_timeout_after_successful_producer_preserves_generic_diagnosis(self):
        status = {'state': 'timeout', 'phase': 'evaluating',
                  'phase_history': {'running': {'exit_code': 0}, 'evaluating': {}}}
        observations = experience.diagnose(status, {}, '')
        self.assertEqual([item['code'] for item in observations],
                         ['shared_wall_deadline', 'producer_completed_evaluation_timeout'])
        precise = observations[1]
        self.assertEqual(precise['fix_status'], 'requires_controller_review')
        self.assertIn('not a completed scientific result', precise['observed_cause'])
        for instruction in ('First verify saved model, predictions and frozen source hashes',
                            'manifest completeness', 'bounded checkpoint-only evaluation',
                            'do not automatically retry', 'assume recovery is possible', 'Preserve the timeout'):
            self.assertIn(instruction, precise['next_step'])

    def test_evaluation_timeout_does_not_infer_missing_or_failed_producer_success(self):
        valid = {'state': 'timeout', 'phase': 'evaluating',
                 'phase_history': {'running': {'exit_code': 0}}}
        variants = [
            {**valid, 'state': 'failed'}, {**valid, 'state': 'completed'},
            {**valid, 'phase': 'running'}, {**valid, 'phase': None},
            {**valid, 'phase_history': None}, {**valid, 'phase_history': {}},
            {**valid, 'phase_history': {'running': None}},
            {**valid, 'phase_history': {'running': {}}},
            {**valid, 'phase_history': {'running': {'exit_code': None}}},
            {**valid, 'phase_history': {'running': {'exit_code': 1}}},
            {**valid, 'phase_history': {'running': {'exit_code': -signal.SIGKILL}}},
        ]
        for status in variants:
            with self.subTest(status=status):
                codes = [item['code'] for item in experience.diagnose(status, {}, '')]
                self.assertNotIn('producer_completed_evaluation_timeout', codes)
                if status['state'] == 'timeout':
                    self.assertIn('shared_wall_deadline', codes)

    def test_experience_backfill_is_idempotent_and_rejects_changed_local_evidence(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'RESEARCH_STATE_DIR': temp}):
            folder = Path(temp)/'collected';folder.mkdir()
            status = {'state': 'failed', 'exit_code': -signal.SIGXCPU}
            (folder/'spec.json').write_text(json.dumps({'kind': 'toolchain_test'}))
            (folder/'status.json').write_text(json.dumps(status))
            collection = {'status': status, 'files': [{'path': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                                    for p in folder.iterdir()]}
            with connect() as db:
                first = experience.record_job_experience(db, 'test', 'a'*32, folder, collection)
                second = experience.record_job_experience(db, 'test', 'a'*32, folder, collection)
                self.assertEqual(first, second)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='job_experience'").fetchone()[0], 1)
            (folder/'status.json').write_text('{}')
            with connect() as db, self.assertRaisesRegex(ValueError, 'source changed'):
                experience.record_job_experience(db, 'test', 'a'*32, folder, collection)

    def run_worker(self, script, timeout=4, evaluator=''):
        """Real isolated CPU toolchain fixture; never a research result."""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            spec = {'kind': 'toolchain_test', 'timeout_seconds': timeout, 'memory_mb': 512, 'gpu': None}
            (folder/'script.py').write_text(script)
            if evaluator:
                (folder/'evaluate.py').write_text(evaluator)
                spec.update(evaluator='evaluate.py', input_sha256={
                    'evaluate.py': hashlib.sha256(evaluator.encode()).hexdigest()})
            (folder/'spec.json').write_text(json.dumps(spec))
            (folder/'config.json').write_text('{}')
            program = 'from pathlib import Path; from tools.harness.remote_worker import supervise; supervise(Path('+repr(temp)+'))'
            subprocess.run([sys.executable, '-c', program], check=True, timeout=12, capture_output=True)
            return json.loads((folder/'status.json').read_text())

    def test_real_cpu_signal_has_actionable_diagnosis_and_no_gpu_time(self):
        status = self.run_worker('import resource\nresource.setrlimit(resource.RLIMIT_CPU,(1,2))\nwhile True: pass\n')
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['failure_code'], 'process_cpu_limit')
        self.assertNotIn('gpu_lease_seconds', status)
        self.assertGreater(status['phase_history']['running']['elapsed_wall_seconds'], .5)

    def test_real_two_phase_timing_does_not_include_queue_or_infer_gpu_use(self):
        script = 'import time\ntime.sleep(.05)\n'
        status = self.run_worker(script, evaluator=script)
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(status['metrics_authority'], 'evaluate.py')
        self.assertEqual(set(status['phase_history']), {'running', 'evaluating'})
        phase_seconds = sum(x['elapsed_wall_seconds'] for x in status['phase_history'].values())
        self.assertGreaterEqual(status['execution_wall_seconds'], phase_seconds)
        self.assertEqual(status['resource_wait_seconds'], 0.)
        self.assertNotIn('gpu_lease_seconds', status)
        self.assertTrue(status['limit_semantics']['cpu_counts_all_threads'])


if __name__ == '__main__':
    unittest.main()
