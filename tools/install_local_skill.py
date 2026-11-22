"""Install shared research skills and reversible compatibility entries; no dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

LINKED = ("research-workflow", "ai2research")
ALIASES = ("brainstorming-research-ideas", "creative-thinking-for-research", "ml-paper-writing", "systems-paper-writing")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".skill-sync-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _check_unlinked_resource(destination, skills):
    if destination.is_symlink() or skills.is_symlink() or any(
            parent.is_symlink() for parent in destination.parents if parent != skills and skills in parent.parents):
        raise ValueError(f"Linked skill resource preserved: {destination.relative_to(skills)}")


def sync_skills(root, codex_home, dry_run=False, restore=False):
    root, codex_home = Path(root).resolve(), Path(codex_home).resolve()
    skills = codex_home / "skills"
    archive = codex_home / "skill-backups" / "research-workflow"
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}, "links": {}}
    operations = []

    if restore:
        for relative, entry in manifest["files"].items():
            destination = skills / relative
            _check_unlinked_resource(destination, skills)
            current = _sha(destination.read_bytes()) if destination.is_file() else None
            if current == entry["original_sha256"]:
                continue
            if current != entry["managed_sha256"]:
                raise ValueError(f"Local changes preserved; cannot restore {relative}")
            if entry["backup"] is not None:
                backup = archive / entry["backup"]
                raw = backup.read_bytes()
                if _sha(raw) != entry["original_sha256"]:
                    raise ValueError(f"Backup changed: {relative}")
                operations.append(("restore", destination, raw))
            else:
                operations.append(("remove_managed_file", destination, None))
        for name, source in manifest.get("links", {}).items():
            destination = skills / name
            if not destination.exists() and not destination.is_symlink():
                continue
            if not destination.is_symlink() or destination.resolve() != Path(source):
                raise ValueError(f"Local changes preserved; cannot remove link {name}")
            operations.append(("remove_managed_link", destination, None))
    else:
        for name in LINKED:
            source, destination = root / "skills" / name, skills / name
            if not (source / "SKILL.md").is_file():
                raise ValueError(f"Missing canonical skill: {name}")
            if destination.is_symlink() and destination.resolve() == source:
                continue
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"Existing unrelated skill preserved: {name}")
            operations.append(("link", destination, source))
            manifest["links"][name] = str(source)

        replacements = {}
        for name in ALIASES:
            folder = skills / name
            if folder.is_symlink():
                raise ValueError(f"Linked third-party skill preserved: {name}")
            for relative in ("SKILL.md", "agents/openai.yaml"):
                replacements[f"{name}/{relative}"] = (root / "skills" / name / relative).read_bytes()
        # Fix a dangling route without changing Nature's specialized resources.
        nature = skills / "nature-polishing" / "SKILL.md"
        if nature.is_file():
            raw = nature.read_bytes()
            updated = raw.replace(b"Use nature-writing when the main task is drafting new sections or rebuilding the manuscript argument.",
                                  b"Use research-workflow when the main task is drafting new sections or rebuilding the manuscript argument.")
            if updated != raw or "nature-polishing/SKILL.md" in manifest["files"]:
                replacements["nature-polishing/SKILL.md"] = updated

        for relative, desired in replacements.items():
            destination = skills / relative
            _check_unlinked_resource(destination, skills)
            existing = destination.read_bytes() if destination.exists() else None
            entry = manifest["files"].get(relative)
            if existing == desired:
                continue
            current = _sha(existing) if existing is not None else None
            if entry:
                accepted = {entry["managed_sha256"]}
                if entry.get("pending"):
                    accepted.update((entry["original_sha256"], entry.get("previous_sha256")))
                if current not in accepted:
                    raise ValueError(f"Local changes preserved; review before updating {relative}")
            if not entry:
                digest = _sha(existing) if existing is not None else None
                backup_name = f"originals/{relative}/{digest}" if digest else None
                entry = {"backup": backup_name, "original_sha256": digest}
                if existing is not None:
                    operations.append(("backup", archive / backup_name, existing))
            manifest["files"][relative] = {**entry, "managed_sha256": _sha(desired),
                                           "previous_sha256": current, "pending": True}
            operations.append(("write", destination, desired))

    report = [{"action": action, "path": str(path)} for action, path, _ in operations]
    if dry_run:
        return {"dry_run": True, "operations": report, "backup_manifest": str(manifest_path)}
    # Save originals, then a write-ahead mapping. An interrupted application can
    # resume from either original or managed bytes without losing the backup.
    if not restore:
        for action, path, value in operations:
            if action == "backup":
                _write(path, value)
        _write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    for action, path, value in operations:
        if action == "backup":
            continue
        if action == "link":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(value, target_is_directory=True)
        elif action in ("remove_managed_file", "remove_managed_link"):
            path.unlink()
        else:
            _write(path, value)
    if restore:
        _write(archive / "restored-manifest.json", json.dumps(manifest, indent=2).encode())
        manifest_path.unlink(missing_ok=True)
    else:
        for entry in manifest["files"].values():
            entry.pop("previous_sha256", None)
            entry["pending"] = False
        _write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return {"dry_run": False, "restored": restore, "operations": report, "backup_manifest": str(manifest_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restore", action="store_true", help="Restore originals without overwriting later local edits")
    args = parser.parse_args()
    result = sync_skills(Path(__file__).resolve().parents[1], args.codex_home, args.dry_run, args.restore)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
