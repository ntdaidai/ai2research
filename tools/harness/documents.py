"""Explicit local-paper text extraction using installed tools, with source provenance."""
import hashlib
import shutil
import subprocess
from pathlib import Path


def extract_paper_text(project_id: str, source_path: str) -> dict:
    from .workspace import project_root, project_status, register_artifact
    project_status(project_id)
    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in (".pdf", ".txt", ".md", ".tex"):
        raise ValueError("Select a local PDF, Markdown, text or TeX paper")
    if not 0 < source.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError("Paper must contain 1 byte to 64 MiB")
    raw = source.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    root = project_root(project_id)
    papers = root / "papers"
    if papers.is_symlink() or papers.resolve().parent != root.resolve():
        raise ValueError("Unsafe paper directory")
    papers.mkdir(exist_ok=True)
    if source.suffix.lower() == ".pdf":
        executable = shutil.which("pdftotext")
        if not executable:
            return {"status": "unavailable", "parser": "pdftotext", "reason": "Install Poppler in the user environment or use the explicit optional Docling adapter"}
        process = subprocess.run([executable, "-layout", "-enc", "UTF-8", str(source), "-"],
                                 capture_output=True, timeout=60)
        if process.returncode:
            return {"status": "failed", "parser": "pdftotext", "returncode": process.returncode,
                    "source_sha256": sha}
        text = process.stdout.decode("utf-8", errors="strict")
        parser = "pdftotext -layout"
    else:
        text = raw.decode("utf-8-sig")
        parser = "utf-8 text"
    if not text.strip():
        return {"status": "needs_ocr", "parser": parser, "source_sha256": sha,
                "reason": "No usable text layer; no OCR was performed"}
    if len(text.encode()) > 8 * 1024 * 1024:
        raise ValueError("Extracted text exceeds 8 MiB; no truncated result was published")
    original = papers / (sha[:20] + source.suffix.lower())
    target = papers / (sha[:20] + ".extracted.txt")
    for path, content in ((original, raw), (target, text.encode("utf-8"))):
        if path.is_symlink():
            raise ValueError("Paper output is a symlink")
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Existing extraction differs; keep it intact and inspect the parser version")
        if not path.exists():
            with path.open("xb") as out:
                out.write(content)
    source_artifact = register_artifact(project_id, original.relative_to(root).as_posix(), "seed_paper")
    text_artifact = register_artifact(project_id, target.relative_to(root).as_posix(), "paper_text")
    return {"status": "completed", "parser": parser, "source_sha256": sha,
            "source_artifact": source_artifact, "text_artifact": text_artifact, "characters": len(text),
            "coverage": "Text layer only; figures and table structure require original-paper inspection"}
