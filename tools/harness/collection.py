"""Validate a collection fully before publishing any canonical local artifact."""
import base64
import hashlib
import json
import math
import os
import re
import shutil
import socket
import tempfile
import time
import uuid
from pathlib import Path
from .state import local_dir


def validate_collection(job_id, result, registered):
    if result.get("job_id") != job_id or result.get("status", {}).get("job_id") != job_id:
        raise ValueError("Returned job identity mismatch")
    decoded, files, total = {}, [], 0
    for item in result["files"]:
        name = item["path"]
        path = Path(name)
        if not name or path.is_absolute() or path.drive or ".." in path.parts or "\\" in name or ":" in name or name in decoded:
            raise ValueError("Unsafe or duplicate returned artifact path")
        raw = base64.b64decode(item["base64"], validate=True)
        total += len(raw)
        if len(raw) > 16 * 1024 * 1024 or total > 32 * 1024 * 1024:
            raise ValueError("Returned artifacts exceed collection budget")
        if hashlib.sha256(raw).hexdigest() != item["sha256"] or item.get("bytes", len(raw)) != len(raw):
            raise ValueError("Artifact checksum failed")
        decoded[name] = raw
        files.append({"path": name, "sha256": item["sha256"], "bytes": len(raw)})
    if not {"spec.json", "script.py", "config.json", "status.json"}.issubset(decoded):
        raise ValueError("Collection is missing required provenance files")
    spec = json.loads(decoded["spec.json"])
    if json.loads(decoded['status.json']) != result['status']:
        raise ValueError('Returned status disagrees with saved supervisor status')
    if registered.get("spec") and spec != registered["spec"]:
        raise ValueError("Returned job spec differs from submitted snapshot")
    expected = dict(spec.get("input_sha256", {}))
    if spec.get("script_sha256"):
        expected["script.py"] = spec["script_sha256"]
    for name, digest in expected.items():
        if name not in decoded or hashlib.sha256(decoded[name]).hexdigest() != digest:
            raise ValueError("Submitted input changed or missing: " + name)
    parameters = json.loads(decoded["config.json"])
    if registered.get("parameters") is not None and parameters != registered["parameters"]:
        raise ValueError("Executed configuration changed after submission")
    metrics = result.get("metrics", {})
    if not isinstance(metrics, dict) or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in metrics.values()):
        raise ValueError("Returned metrics are invalid")
    if not result.get("metrics_error"):
        saved = json.loads(decoded.get("metrics.json", b"{}"))
        if metrics != saved.get("metrics", {}):
            raise ValueError("Metrics disagree with saved evaluator output")
    if spec.get("evaluator") and result["status"]["state"] == "completed" and not result.get("metrics_error"):
        if result["status"].get("metrics_authority") != spec["evaluator"] or not metrics:
            raise ValueError("Final metrics did not come from the frozen evaluator")
    return decoded, files


def publish_collection(job_id, decoded):
    root = local_dir()
    folder = root / "jobs" / job_id
    if folder.resolve() != folder or folder.parent.resolve() != root / "jobs":
        raise ValueError("Canonical collection directory is unsafe")
    staging_root = root / ".collections"
    if staging_root.resolve() != staging_root:
        raise ValueError("Collection staging directory is unsafe")
    staging_root.mkdir(parents=True, exist_ok=True)
    owner = None
    try:
        with tempfile.TemporaryDirectory(prefix="collection-", dir=staging_root) as scratch:
            stage = Path(scratch)
            owner = stage.with_name(stage.name + ".owner.json")
            with owner.open("x", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "hostname": socket.gethostname(), "created_unix": time.time()}, handle)
            for name, raw in decoded.items():
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            folder.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if folder.exists():
                # Keep an interrupted old publication for diagnosis, rather than deleting it.
                backup = root / ".collection-recovery" / (job_id + "-" + uuid.uuid4().hex)
                backup.parent.mkdir(parents=True, exist_ok=True)
                if backup.resolve().parent != root / ".collection-recovery":
                    raise ValueError("Unsafe recovery directory")
                folder.rename(backup)
            try:
                stage.rename(folder)
            except BaseException:
                if backup is not None and not folder.exists():
                    backup.rename(folder)
                raise
    finally:
        # Retain the marker if temporary-directory cleanup itself failed.
        if owner is not None and not stage.exists():
            owner.unlink(missing_ok=True)
    return folder


def cleanup_abandoned_collections(min_age_seconds=86400, limit=32):
    """Remove registered staging leftovers only after their local owner is gone."""
    if (isinstance(min_age_seconds, bool) or not isinstance(min_age_seconds, (int, float))
            or not math.isfinite(min_age_seconds) or min_age_seconds < 0):
        raise ValueError("min_age_seconds must be a finite nonnegative number")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("limit must be an integer from 1 to 256")
    root = local_dir() / ".collections"
    summary = {"examined": 0, "removed_stages": 0, "removed_owners": 0, "retained": 0, "failures": {}}
    if root.is_symlink() or root.resolve() != root:
        summary["failures"]["UnsafeStagingDirectory"] = 1
        return summary
    if not root.exists():
        return summary
    hostname, current = socket.gethostname(), time.time()
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                match = re.fullmatch(r"(collection-[a-z0-9_]{8})\.owner\.json", entry.name)
                if not match:
                    continue
                if summary["examined"] >= limit:
                    break
                summary["examined"] += 1
                owner, stage = Path(entry.path), root / match.group(1)
                try:
                    if owner.is_symlink() or stage.is_symlink() or not owner.is_file():
                        raise ValueError("Unsafe collection owner or stage")
                    if owner.stat().st_size > 4096:
                        raise ValueError("Oversized collection owner")
                    data = json.loads(owner.read_text(encoding="utf-8"))
                    pid, created = data["pid"], data["created_unix"]
                    if (type(pid) is not int or pid <= 0 or isinstance(created, bool)
                            or not isinstance(created, (int, float)) or not math.isfinite(created)):
                        raise ValueError("Invalid collection owner")
                    if data["hostname"] != hostname or current - created < min_age_seconds:
                        summary["retained"] += 1
                        continue
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    except (PermissionError, OverflowError):
                        summary["retained"] += 1
                        continue
                    else:
                        summary["retained"] += 1
                        continue
                    if stage.exists():
                        shutil.rmtree(stage)
                        summary["removed_stages"] += 1
                    owner.unlink()
                    summary["removed_owners"] += 1
                except FileNotFoundError:
                    # Another maintenance caller may have cleaned this owner already.
                    continue
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    kind = type(exc).__name__
                    summary["failures"][kind] = summary["failures"].get(kind, 0) + 1
    except OSError as exc:
        summary["failures"][type(exc).__name__] = 1
    return summary
