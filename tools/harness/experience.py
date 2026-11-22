"""Deterministic observations from verified jobs, without model calls or retries.

An observed failure is not a verified fix. The live controller records the code
change and its validation separately; this module never edits experimental code,
changes resource limits, or infers scientific success from process completion.
"""
from __future__ import annotations

import hashlib
import json
import signal
import uuid
from pathlib import Path

from .state import local_dir, now


def diagnose(status, spec, logs, metrics_error=None):
    observations = []

    def add(code, cause, next_step):
        observations.append({'code': code, 'observed_cause': cause, 'next_step': next_step,
                             'fix_status': 'requires_controller_review'})

    if status.get('exit_code') == -signal.SIGXCPU or status.get('failure_code') == 'process_cpu_limit':
        add('process_cpu_limit', 'SIGXCPU is the process CPU-time limit, summed over all threads.',
            'Measure data preparation, forward/backward and optimizer CPU seconds; optimize the bottleneck before preparing a concrete revised limit. Preserve the failed run.')
    if status.get('exit_code') == -signal.SIGXFSZ or status.get('failure_code') == 'file_size_limit':
        add('single_file_limit', 'The worker limits each file to 16 MiB.',
            'Shard model data inside the job and verify every shard; retain a small manifest for bounded collection.')
    if 'ModuleNotFoundError:' in logs or 'ImportError:' in logs:
        add('python_import_failure', 'The frozen runtime could not import a dependency.',
            'Validate the archive-only environment and import cache/path ordering; repair an isolated bundle, freeze it, and use a new recorded run.')
    if 'Predictions disagree with independently scored saved model' in logs:
        add('independent_score_mismatch', 'Saved predictions failed independent scoring.',
            'Compare normalization, saved model and feature identities; do not accept the claimed scores or hotpatch the running job.')
    if 'Submitted input changed:' in logs or 'Submitted configuration changed' in logs:
        add('frozen_input_changed', 'Frozen input/configuration integrity check failed.',
            'Preserve the failure and prepare a new immutable proposal; never reuse its old approval to alter fixed evaluation.')
    if status.get('state') == 'resource_unavailable':
        add('resource_wait_exhausted', 'No device satisfied the existing resource and own-lease checks.',
            'Recheck actual available resources and queue a bounded task; do not weaken shared-user protections or count queue time as GPU compute.')
    if status.get('state') == 'timeout':
        add('shared_wall_deadline', 'Execution and independent evaluation share a wall-clock deadline.',
            'Measure both phases and reserve evaluation time in a concrete bounded plan; distinguish wall time from threaded CPU time.')
        phases = status.get('phase_history')
        producer = phases.get('running') if isinstance(phases, dict) else None
        if (status.get('phase') == 'evaluating' and isinstance(producer, dict)
                and producer.get('exit_code') == 0):
            add('producer_completed_evaluation_timeout',
                'The producer exited successfully, but independent evaluation did not finish before the shared deadline. This is not a completed scientific result.',
                'First verify saved model, predictions and frozen source hashes and manifest completeness. If those checks support recovery, prepare a separately frozen, bounded checkpoint-only evaluation; do not automatically retry or assume recovery is possible. Preserve the timeout.')
    if status.get('state') == 'resource_limit':
        add('sampled_memory_limit', 'The worker observed memory beyond its approved process-group budget.',
            'Inspect peak usage and reduce batch/cache/model memory before a separately frozen rerun.')
    if status.get('exit_code') == -signal.SIGKILL and not observations:
        add('unexplained_sigkill', 'The child received SIGKILL; the cause is not established.',
            'Inspect supervisor/resource evidence; do not label SIGKILL alone as an OOM diagnosis.')
    if status.get('state') == 'completed' and metrics_error:
        add('invalid_or_missing_metrics', 'Process completion did not produce valid authoritative metrics.',
            'Repair the evaluator/collection contract and rerun under a frozen plan; do not count this as a completed research result.')
    if status.get('state') == 'failed' and not observations:
        add('unclassified_execution_failure', 'The execution failed without a supported deterministic diagnosis.',
            'Read the preserved verified logs and record a concrete root cause before changing code or retrying.')
    return observations


def record_job_experience(db, project_id, job_id, folder, collection):
    """Called within the collector's transaction, including idempotent backfills."""
    folder = Path(folder)
    expected = {item['path']: item['sha256'] for item in collection.get('files', [])}
    for name in ('spec.json', 'config.json', 'status.json', 'run.log', 'evaluate.log', 'metrics.json'):
        if name in expected:
            path = folder/name
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
                raise ValueError('Verified experience source changed: ' + name)
    spec = json.loads((folder/'spec.json').read_text())
    status = collection['status']
    evidence, texts = {}, []
    for name in ('spec.json', 'config.json', 'status.json', 'run.log', 'evaluate.log', 'metrics.json'):
        path = folder/name
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
        evidence[name] = digest.hexdigest()
        if name.endswith('.log'):
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size-65536))
                texts.append(stream.read(65536).decode('utf-8', errors='replace'))
    document = {'schema_version': 1, 'job_id': job_id, 'project_id': project_id,
                'evidence_kind': spec.get('kind'), 'evidence_directory': str(folder),
                'source_sha256': evidence, 'state': status['state'],
                'observations': diagnose(status, spec, '\n'.join(texts), collection.get('metrics_error')),
                'resource_measurements': {key: status[key] for key in
                    ('resource_wait_seconds', 'execution_wall_seconds', 'gpu_lease_seconds',
                     'phase_history', 'limit_semantics', 'peak_gpu_memory_mb', 'peak_host_rss_mb') if key in status},
                'historical_timing_limit': 'Missing phase/GPU acquisition times remain unknown; dispatch-to-finish is not GPU runtime.',
                'scientific_conclusion': 'Not inferred from exit status; controlled metrics, replication and reviews remain necessary.'}
    raw = (json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()
    fingerprint = hashlib.sha256(raw).hexdigest()
    target = local_dir()/'experiences'/job_id/(fingerprint+'.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(raw)
        temporary.replace(target)
    elif target.read_bytes() != raw:
        raise ValueError('Stored experience digest mismatch')
    reference = {'job_id': job_id, 'path': str(target), 'sha256': fingerprint,
                 'observation_codes': [item['code'] for item in document['observations']]}
    prior = db.execute("SELECT payload FROM events WHERE project=? AND kind='job_experience'", (project_id,)).fetchall()
    if not any(json.loads(row[0]).get('sha256') == fingerprint for row in prior):
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                   (uuid.uuid4().hex, project_id, 'job_experience', now(), json.dumps(reference)))
    return reference
