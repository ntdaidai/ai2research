"""Build an ai2research source archive using Git's ignore rules and a source allowlist."""
import argparse
import hashlib
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitattributes", ".gitignore", "AGENTS.md", "HANDOFF.md", "README.md", "RESEARCH_PLAN.md", "ai2research", "harness.toml",
    "project.json", "requirements-harness.txt", "requirements-harness.lock.txt", "requirements-arxiv.lock.txt",
}
SOURCE_SUFFIXES = {
    "tools": {".py", ".sh"},
    "tests": {".py", ".mjs"},
    "research": {".md", ".json"},
    "protocol": {".md"},
    "templates": {".md", ".tex", ".drawio"},
    "workflow": {".json"},
    "skills": {".md", ".yaml"},
    "web": {".html", ".css", ".js", ".svg", ".md"},
}
EMPTY_DIR_MARKERS = {"runs/.gitkeep", "data/raw/.gitkeep", "data/processed/.gitkeep"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="New .tar.gz output path")
    target = parser.parse_args().archive.resolve()
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    files = []
    for name in sorted(set(names) - {""}):
        source = ROOT / name
        if name not in ROOT_FILES | EMPTY_DIR_MARKERS:
            parts = Path(name).parts
            if any(part.startswith(".") for part in parts) or source.suffix not in SOURCE_SUFFIXES.get(parts[0], ()):
                continue
        if source.resolve() != source or source.is_symlink() or not source.is_file():
            raise ValueError(f"Expected a regular project file: {name}")
        files.append((source, name))
    if not ROOT_FILES.issubset({name for _, name in files}):
        raise ValueError("ai2research source files are missing")

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as destination, tarfile.open(fileobj=destination, mode="w:gz") as archive:
        for source, name in files:
            info = archive.gettarinfo(str(source), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if name == "ai2research" or source.suffix == ".sh" else 0o644
            with source.open("rb") as contents:
                archive.addfile(info, contents)
    with target.open("rb") as archive:
        digest = hashlib.file_digest(archive, "sha256").hexdigest()
    print(f"{digest}  {target} ({len(files)} source files)")


if __name__ == "__main__":
    main()
