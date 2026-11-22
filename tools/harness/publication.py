"""Chapter-level publication tasks, measured figures and auditable delivery bundles.

This module coordinates artifacts; prose and research decisions remain with the
active controller and its assigned agents.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from .state import connect, now, record


CHAPTERS = {
    "methods": ("Method", []),
    "experiment_setup": ("Experimental Setup", []),
    "results": ("Results and Analysis", ["methods", "experiment_setup"]),
    "related_work": ("Related Work", []),
    "introduction": ("Introduction", ["methods", "results"]),
    "discussion": ("Discussion and Limitations", ["results"]),
    "conclusion": ("Conclusion", ["results", "discussion"]),
    "abstract": ("Abstract", ["introduction", "results", "conclusion"]),
}
READING_ORDER = ("introduction", "related_work", "methods", "experiment_setup", "results", "discussion", "conclusion")
NONRESEARCH = {"toolchain_test", "synthetic", "synthetic_toolchain_test", "synthetic_workflow_test"}
CODE_KINDS = {"code", "source_code", "experiment_code", "method_code"}
RESULT_KINDS = {"results", "experiment_result", "experiment_results", "experiment_matrix", "confirmed_results", "metrics"}
CONFIRMATION_KINDS = {"verification_results", "replication_results", "independent_check", "ablation_results", "ablation"}
RENDERED_FIGURE_KINDS = {"figure_png", "figure_pdf", "diagram_pdf", "diagram_svg"}
LOCAL_TEX_SUFFIXES = {".tex", ".bib", ".cls", ".sty"}
RENDER_MANIFEST = "paper/render_manifest.json"
SECRET_PATTERN = re.compile(rb"sk-(?:ws-)?[A-Za-z0-9_.-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def _workspace():
    from . import workspace
    return workspace


def _safe_path(root: Path, relative: str, must_exist: bool = True) -> Path:
    rel = Path(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Expected a project-relative path")
    target = root / rel
    for item in [target, *target.parents]:
        if item == root.parent:
            break
        if item.is_symlink():
            raise ValueError("Symbolic links are not publication inputs")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes project workspace")
    if must_exist and not target.is_file():
        raise ValueError("Missing publication file: " + relative)
    return target


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _latest_artifacts(project_id: str) -> dict:
    result = {}
    # Registration sequence is authoritative; updated versions supersede old
    # versions of the same relative file, while review packets retain old IDs.
    for item in _workspace().list_artifacts(project_id):
        result[item["path"]] = item
    return result


def _verify_artifact(root: Path, item: dict) -> Path:
    path = _safe_path(root, item["path"])
    if _sha(path) != item["sha256"]:
        raise ValueError("Registered artifact changed: " + item["path"])
    return path


def _evidence_kind(project_id: str, artifacts: list[dict] | None = None) -> str:
    state = _workspace().project_status(project_id)
    project = state.get("manifest", state.get("project", state))
    kinds = {project.get("evidence_kind", "research")}
    kinds.update(item.get("evidence_kind", "research") for item in artifacts or [])
    return "toolchain_test" if kinds & NONRESEARCH else "research"


def publication_plan(project_id: str, venue_style: str = "generic") -> dict:
    """Create an idempotent dependency graph of separate chapter assignments."""
    if venue_style not in {"generic", "ml", "systems"}:
        raise ValueError("venue_style must be generic, ml or systems")
    ws = _workspace()
    root = ws.project_root(project_id)
    spec_path = _safe_path(root, "paper/plan.json", False)
    if spec_path.exists():
        previous = json.loads(spec_path.read_text())
        if previous["venue_style"] != venue_style:
            raise ValueError("A publication plan already exists with another style")
        return previous
    paper = root / "paper"
    (paper / "sections").mkdir(parents=True, exist_ok=True)
    evidence = _latest_artifacts(project_id)
    for item in evidence.values():
        _verify_artifact(root, item)
    context = {
        "project_id": project_id, "venue_style": venue_style,
        "terminology": {}, "claims": [],
        "instructions": "Controller maintains terminology and claim-to-artifact links. Empty claims are not scientific evidence.",
        "locked_artifacts": [{k: a[k] for k in ("artifact_id", "path", "sha256")} for a in evidence.values()],
    }
    context_path = _safe_path(root, "paper/context.json", False)
    if not context_path.exists():
        _json(context_path, context)
    writing_skill = "systems-paper-writing" if venue_style == "systems" else "ml-paper-writing"
    tasks = {}
    for chapter, (title, dependencies) in CHAPTERS.items():
        output = f"paper/sections/{chapter}.tex"
        instructions = (
            f"Write only the {title} chapter to {output}. Read paper/context.json, the locked artifacts, "
            "and completed dependency chapters. Preserve terminology and cite verified bibliography keys in paper/references.bib. "
            "Tie numerical statements to artifact IDs. If evidence is absent, report what is missing instead of inventing prose or numbers. "
            f"Use ${writing_skill}; use $anti-defensive-writing and $nature-polishing for revision. "
            "The abstract file contains only abstract prose; other files include their own section heading. "
            "Register the chapter artifact and complete only this task after checking its evidence. "
            "Existing result snapshots are context, not approval to run experiments."
        )
        task = ws.create_task(project_id, title=f"Write chapter: {title}", role="chapter_writer",
                              dependencies=[tasks[name]["task_id"] for name in dependencies],
                              instructions=instructions, output_paths=[output], task_key="chapter-" + chapter)
        tasks[chapter] = {"task_id": task["task_id"], "path": output, "dependencies": dependencies}
    plan = {"project_id": project_id, "venue_style": venue_style, "created_utc": now(),
            "context": "paper/context.json", "chapters": tasks,
            "bibliography": "paper/references.bib", "main": "paper/main.tex",
            "evidence_kind": _evidence_kind(project_id, list(evidence.values())),
            "status": "planned", "template_note": "Generic article; replace with verified official venue template before submission."}
    _json(spec_path, plan)
    ws.register_artifact(project_id, "paper/plan.json", "publication_plan", plan["evidence_kind"])
    ws.register_artifact(project_id, "paper/context.json", "publication_context", plan["evidence_kind"])
    return plan


def _chapter_readiness(project_id: str) -> tuple[dict, list[str]]:
    root = _workspace().project_root(project_id)
    path = _safe_path(root, "paper/plan.json", False)
    if not path.is_file():
        return {}, ["publication_plan has not been created"]
    plan = json.loads(path.read_text())
    state = _workspace().project_status(project_id)
    tasks = {t["task_id"]: t for t in state.get("tasks", [])}
    artifacts = _latest_artifacts(project_id)
    missing = []
    for name, chapter in plan["chapters"].items():
        task = tasks.get(chapter["task_id"], {})
        if task.get("state", task.get("status")) != "completed":
            missing.append("chapter task is not completed: " + name)
        chapter_path = _safe_path(root, chapter["path"], False)
        if not chapter_path.is_file():
            missing.append("missing chapter: " + chapter["path"])
            continue
        text = chapter_path.read_text(encoding="utf-8")
        meaningful = re.sub(r"(?m)%.*$", "", text).strip()
        if len(meaningful) < 40 or re.search(r"(?im)^\s*(?:TODO|TBD|PLACEHOLDER|待填写|待补充)\s*[:：]", meaningful):
            missing.append("chapter is empty or unfinished: " + name)
        item = artifacts.get(chapter["path"])
        if not item:
            missing.append("chapter is not registered: " + name)
        else:
            _verify_artifact(root, item)
    bib = _safe_path(root, plan["bibliography"], False)
    if not bib.is_file() or not bib.read_text().strip():
        missing.append("missing nonempty bibliography: " + plan["bibliography"])
    return plan, missing


def _build_input_paths(root: Path, plan: dict, artifacts: dict) -> set[str]:
    """Track paper text and explicit project-local TeX/graphics dependencies.

    Standard input/include/bibliography/includegraphics paths are discovered
    recursively from the main-file working directory. This is dependency
    bookkeeping, not execution or arbitrary dynamic TeX macro expansion.
    """
    inputs = {plan.get("main", "paper/main.tex"), plan.get("bibliography", "paper/references.bib"),
              *[chapter["path"] for chapter in plan.get("chapters", {}).values()]}
    context = plan.get("context", "paper/context.json")
    if _safe_path(root, context, False).is_file():
        inputs.add(context)
    inputs.update(relative for relative, artifact in artifacts.items() if artifact["kind"] in RENDERED_FIGURE_KINDS)
    paper = _safe_path(root, "paper", False)
    if paper.is_dir():
        for path in paper.rglob("*"):
            if path.is_symlink():
                raise ValueError("Symbolic links are not publication inputs")
            if "build" in path.relative_to(paper).parts:
                continue
            if path.is_file() and path.suffix.lower() in LOCAL_TEX_SUFFIXES:
                relative = path.relative_to(root).as_posix()
                _safe_path(root, relative)
                inputs.add(relative)
    scanned, texts = set(), []
    while True:
        pending = [relative for relative in inputs - scanned if Path(relative).suffix.lower() in LOCAL_TEX_SUFFIXES]
        if not pending:
            break
        for relative in pending:
            scanned.add(relative)
            source = _safe_path(root, relative, False)
            if not source.is_file():
                continue
            text = re.sub(r"(?m)(?<!\\)%.*$", "", source.read_text(encoding="utf-8"))
            texts.append(text)
            # TeX resolves these paths from the main file's working directory.
            for name in re.findall(r"\\(?:input|include)\s*\{([^{}]+)\}", text):
                try:
                    inputs.add(_tex_reference(root, paper, name, ("", ".tex")))
                except FileNotFoundError:
                    # Installed TeX packages may input files from the compiler's
                    # own distribution. Only project-local dependencies belong
                    # in this project's build receipt.
                    pass
            for bibliography in re.findall(r"\\bibliography\s*\{([^{}]+)\}", text):
                for name in bibliography.split(","):
                    try:
                        inputs.add(_tex_reference(root, paper, name, ("", ".bib")))
                    except FileNotFoundError:
                        pass
    directories = [paper]
    for text in texts:
        for declared in re.findall(r"\\graphicspath\s*\{((?:\s*\{[^{}]+\}\s*)+)\}", text):
            for directory in re.findall(r"\{([^{}]+)\}", declared):
                directories.append(_tex_reference_path(root, paper, directory))
    for text in texts:
        for name in re.findall(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}", text):
            for directory in directories:
                try:
                    image = _tex_reference(root, directory, name, ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"))
                except FileNotFoundError:
                    continue
                inputs.add(image)
                break
            else:
                raise ValueError("Missing project-local includegraphics dependency: " + name)
    return inputs


def _tex_reference_path(root: Path, base: Path, reference: str) -> Path:
    reference = reference.strip()
    if not reference or Path(reference).is_absolute() or "\\" in reference:
        raise ValueError("TeX dependencies must use explicit project-local relative paths")
    parts = list(base.relative_to(root).parts)
    for part in Path(reference).parts:
        if part == "..":
            if not parts:
                raise ValueError("TeX dependency escapes project workspace")
            parts.pop()
        elif part != ".":
            parts.append(part)
        candidate = root.joinpath(*parts)
        if candidate.is_symlink():
            raise ValueError("Symbolic links are not publication inputs")
    return _safe_path(root, Path(*parts).as_posix(), False)


def _tex_reference(root: Path, base: Path, reference: str, suffixes: tuple[str, ...]) -> str:
    target = _tex_reference_path(root, base, reference)
    for suffix in suffixes:
        candidate = target if not suffix else target.with_name(target.name + suffix)
        relative = candidate.relative_to(root).as_posix()
        candidate = _safe_path(root, relative, False)
        if candidate.is_file():
            return relative
    raise FileNotFoundError("Missing local TeX dependency: " + reference)


def _render_manifest_value(project_id: str, inputs: dict, pdf_sha256: str | None) -> dict:
    return {"schema_version": 1, "project_id": project_id,
            "input_files": [{"path": path, "sha256": digest} for path, digest in sorted(inputs.items())],
            "compiled_pdf": {"path": "paper/paper.pdf", "sha256": pdf_sha256},
            "review_notes": {
                "manifest_generation": "Version inventory only; no visual inspection was performed by generating this manifest.",
                "dependency_coverage": "All paper-local tex/bib/cls/sty sources plus explicit standard input/include/bibliography/includegraphics paths and registered rendered figures; arbitrary dynamic TeX macro expansion is not evaluated.",
                "independent_agent": "Inspect the actual compiled PDF and figures. Record observed legibility, labels, numerical consistency and layout in the agent review, referencing this manifest's artifact ID and the inspected file versions.",
                "external_text_reviewer": "Coverage is the textual dependency inventory and supplied source only; binary figures and PDF pixels are not directly inspected."}}


def _write_render_manifest(project_id: str, root: Path, inputs: dict, kind: str, pdf_sha256: str | None = None) -> dict:
    value = _render_manifest_value(project_id, inputs, pdf_sha256)
    _json(_safe_path(root, RENDER_MANIFEST, False), value)
    return _workspace().register_artifact(project_id, RENDER_MANIFEST, "render_manifest", kind)


def assemble_manuscript(project_id: str, compile_pdf: bool = True) -> dict:
    """Assemble completed chapter files, retaining separate sources and citations."""
    ws = _workspace()
    root = ws.project_root(project_id)
    plan, missing = _chapter_readiness(project_id)
    if missing:
        return {"status": "incomplete", "missing": missing, "project_id": project_id}
    registered = _latest_artifacts(project_id)
    kind = _evidence_kind(project_id, list(registered.values()))
    title = ws.project_status(project_id).get("manifest", {}).get("title") or project_id
    context_relative = plan.get("context", "paper/context.json")
    context_path = _safe_path(root, context_relative, False)
    context = {}
    if context_path.is_file():
        context_artifact = registered.get(context_relative)
        if not context_artifact:
            raise ValueError("Register the publication context before assembly")
        _verify_artifact(root, context_artifact)
        context = json.loads(context_path.read_text(encoding="utf-8"))
        context_title = context.get("title") if isinstance(context, dict) else None
        if isinstance(context_title, str) and context_title.strip():
            title = context_title.strip()
    title = re.sub(r"[\\{}%$&#_^~]", " ", title)
    latex_format = context.get("latex_format", "generic") if isinstance(context, dict) else "generic"
    if not isinstance(latex_format, str) or latex_format not in {"generic", "ieee_conference"}:
        raise ValueError("latex_format must be generic or ieee_conference")
    document_class = r"\documentclass[conference]{IEEEtran}" if latex_format == "ieee_conference" else r"\documentclass[10pt]{article}"
    bibliography_style = "IEEEtran" if latex_format == "ieee_conference" else "plain"
    lines = [document_class, r"\usepackage{graphicx}", r"\usepackage{booktabs}",
             r"\usepackage{amsmath}", r"\usepackage{url}", "\\title{" + title + "}",
             r"\author{}", r"\date{}", r"\begin{document}", r"\maketitle"]
    if latex_format == "ieee_conference":
        # Select the standard TeX encoding before IEEEtran initializes Times;
        # Tectonic's XeTeX otherwise substitutes unavailable TU/ptm shapes.
        lines.insert(0, r"\RequirePackage[T1]{fontenc}")
    if kind in NONRESEARCH:
        lines.append(r"\noindent\textbf{TOOLCHAIN TEST: synthetic infrastructure validation; not a research finding.}")
    preamble = _safe_path(root, "paper/preamble.tex", False)
    if preamble.is_file():
        # Explicit local formatting source is tracked by the existing TeX input
        # scanner and build/review manifests, rather than patching a built PDF.
        lines.insert(lines.index(r"\begin{document}"), r"\input{preamble.tex}")
    lines += [r"\begin{abstract}", r"\input{sections/abstract.tex}", r"\end{abstract}"]
    if latex_format == "ieee_conference" and _safe_path(root, "paper/keywords.tex", False).is_file():
        lines.append(r"\input{keywords.tex}")
    lines.extend("\\input{sections/" + name + ".tex}" for name in READING_ORDER)
    lines += ["\\bibliographystyle{" + bibliography_style + "}", r"\bibliography{references}", r"\end{document}"]
    main = _safe_path(root, plan["main"], False)
    main.write_text("\n".join(lines) + "\n", encoding="utf-8")
    main_artifact = ws.register_artifact(project_id, plan["main"], "manuscript_source", kind)
    ws.register_artifact(project_id, plan["bibliography"], "bibliography", kind)
    artifacts = _latest_artifacts(project_id)
    input_paths = _build_input_paths(root, plan, artifacts)
    for relative in input_paths:
        if relative in artifacts and artifacts[relative]["kind"] in RENDERED_FIGURE_KINDS:
            _verify_artifact(root, artifacts[relative])
        suffix = Path(relative).suffix.lower()
        if suffix in LOCAL_TEX_SUFFIXES:
            dependency_kind = "manuscript_style" if suffix in {".cls", ".sty"} else artifacts.get(relative, {}).get("kind", "manuscript_dependency")
            ws.register_artifact(project_id, relative, dependency_kind, kind)
        elif relative not in artifacts:
            dependency_kind = {".pdf": "figure_pdf", ".png": "figure_png", ".svg": "diagram_svg"}.get(suffix, "manuscript_dependency")
            ws.register_artifact(project_id, relative, dependency_kind, kind)
    receipt = {"project_id": project_id, "evidence_kind": kind, "created_utc": now(),
               "inputs": {p: _sha(_safe_path(root, p)) for p in sorted(input_paths)}}
    if not compile_pdf:
        render_manifest = _write_render_manifest(project_id, root, receipt["inputs"], kind)
        return {"status": "assembled", "main": str(main), "artifact": main_artifact,
                "evidence_kind": kind, "pdf": None, "render_manifest": render_manifest}
    from .service import compile_paper
    result = compile_paper(str(main))
    if result.get("status") != "completed":
        return {"status": result.get("status", "failed"), "main": str(main), "pdf": None, "build": result}
    pdf = _safe_path(root, "paper/paper.pdf", False)
    shutil.copyfile(result["pdf"], pdf)
    if result.get("log"):
        log = _safe_path(root, "paper/build/compile.log", False)
        log.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result["log"], log)
        ws.register_artifact(project_id, "paper/build/compile.log", "build_log", kind)
    receipt["pdf_sha256"] = _sha(pdf)
    render_manifest = _write_render_manifest(project_id, root, receipt["inputs"], kind, receipt["pdf_sha256"])
    _json(_safe_path(root, "paper/build.json", False), receipt)
    pdf_artifact = ws.register_artifact(project_id, "paper/paper.pdf", "manuscript_pdf", kind)
    ws.register_artifact(project_id, "paper/build.json", "manuscript_build", kind)
    return {"status": "completed", "main": str(main), "pdf": str(pdf), "artifact": pdf_artifact,
            "evidence_kind": kind, "build": result, "render_manifest": render_manifest}


def _read_rows(path: Path, metric: str) -> list[dict]:
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    elif path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and "main_results" in value:
            rows = [{"label": f"{row['role']} {row['job_id'][:8]}", metric: row["value"]}
                    for row in value["main_results"] if row.get("metric") == metric]
        else:
            rows = value.get("rows") if isinstance(value, dict) else value
    else:
        raise ValueError("Figure input must be CSV or JSON")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("Figure requires 1–200 rows with label and selected metric")
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("label", "")).strip():
            raise ValueError("Every figure row requires label")
        value = row.get(metric)
        if isinstance(value, bool):
            raise ValueError("Metric must be a finite number")
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError("Missing or non-numeric metric: " + metric) from None
        if not math.isfinite(number):
            raise ValueError("Metric must be finite")
        parsed.append({"label": str(row["label"]), "value": number})
    return parsed


def export_results_figure(project_id: str, source_path: str, metric: str, title: str = "") -> dict:
    """Render registered CSV/JSON results and save a standalone reproduction script.

    Schema: CSV ``label,<metric>``, JSON ``{"rows":[{"label":"...",<metric>:1.2}]}``,
    or the ``main_results`` table returned by build_experiment_matrix.
    Synthetic labels are inherited from the project and source registration.
    """
    if not metric.strip() or len(metric) > 100:
        raise ValueError("Specify a metric name of 1–100 characters")
    ws = _workspace()
    root = ws.project_root(project_id)
    source = _safe_path(root, source_path)
    item = _latest_artifacts(project_id).get(source_path)
    if not item or item["kind"] not in RESULT_KINDS:
        raise ValueError("Register source as a results or experiment_matrix artifact first")
    _verify_artifact(root, item)
    rows = _read_rows(source, metric)
    kind = _evidence_kind(project_id, [item])
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", metric)[:50]
    folder_rel = f"figures/{slug}-{item['sha256'][:12]}"
    folder = _safe_path(root, folder_rel, False)
    folder.parent.mkdir(parents=True, exist_ok=True)
    input_name = "source" + source.suffix.lower()
    # Copy the plotting parser into the script as ordinary Python so exporting
    # does not depend on the framework or the original project absolute path.
    chart_title = title or metric
    if kind in NONRESEARCH:
        chart_title += " [TOOLCHAIN TEST — SYNTHETIC]"
    script = '''from pathlib import Path
import csv, hashlib, json, math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
root = Path(__file__).resolve().parent
source = root / INPUT_NAME
if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
    raise ValueError("Plot source changed")
if source.suffix == ".csv":
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
else:
    data = json.loads(source.read_text())
    if isinstance(data, dict) and "main_results" in data:
        rows = [{"label": str(row["role"])+" "+row["job_id"][:8], METRIC: row["value"]}
                for row in data["main_results"] if row.get("metric") == METRIC]
    else:
        rows = data["rows"] if isinstance(data, dict) else data
labels, values = [str(row["label"]) for row in rows], [float(row[METRIC]) for row in rows]
if not all(math.isfinite(value) for value in values):
    raise ValueError("Non-finite plot metric")
fig, axis = plt.subplots(figsize=(max(5, min(14, len(labels)*0.7)), 3.8))
axis.bar(range(len(values)), values)
axis.set_xticks(range(len(labels)), labels, rotation=30, ha="right")
axis.set_ylabel(METRIC)
axis.set_title(TITLE)
fig.tight_layout()
fig.savefig(root / "figure.png", dpi=180)
fig.savefig(root / "figure.pdf")
plt.close(fig)
'''
    constants = f"INPUT_NAME={input_name!r}\nSOURCE_SHA={item['sha256']!r}\nMETRIC={metric!r}\nTITLE={chart_title!r}\n"
    with tempfile.TemporaryDirectory(prefix=".figure-", dir=folder.parent) as temporary:
        scratch = Path(temporary)
        shutil.copyfile(source, scratch / input_name)
        script_path = scratch / "reproduce.py"
        script_path.write_text(constants + script, encoding="utf-8")
        execution = subprocess.run([sys.executable, str(script_path)], cwd=str(scratch), capture_output=True, timeout=60)
        if execution.returncode:
            raise RuntimeError("Figure rendering failed: " + execution.stderr.decode("utf-8", "replace")[-1000:])
        names = (input_name, "reproduce.py", "figure.png", "figure.pdf")
        for name in names:
            if not (scratch / name).is_file() or (scratch / name).stat().st_size == 0:
                raise ValueError("Figure output missing: " + name)
            _safe_path(root, folder_rel + "/" + name, False)
        folder.mkdir(exist_ok=True)
        for name in names:
            (scratch / name).replace(folder / name)
    outputs = []
    for name, artifact_kind in ((input_name, "figure_data"), ("reproduce.py", "figure_source"),
                                ("figure.png", "figure_png"), ("figure.pdf", "figure_pdf")):
        target = folder / name
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError("Figure output missing: " + name)
        outputs.append(ws.register_artifact(project_id, folder_rel + "/" + name, artifact_kind, kind))
    metadata = {"source_artifact_id": item["artifact_id"], "source_sha256": item["sha256"],
                "metric": metric, "rows": rows, "evidence_kind": kind,
                "outputs": [{"path": a["path"], "sha256": a["sha256"]} for a in outputs]}
    _json(folder / "provenance.json", metadata)
    outputs.append(ws.register_artifact(project_id, folder_rel + "/provenance.json", "figure_provenance", kind))
    return {"status": "completed", "directory": str(folder), "artifacts": outputs,
            "source_sha256": item["sha256"], "evidence_kind": kind}


def _sensitive(path: Path) -> bool:
    if path.name.startswith(".env") or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx"} or path.name in {"id_rsa", "credentials", "credentials.json"}:
        return True
    with path.open("rb") as stream:
        previous = b""
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if SECRET_PATTERN.search(previous + chunk):
                return True
            previous = chunk[-256:]
    return False


def export_project_diagram(project_id: str, source_path: str, output_format: str = "svg") -> dict:
    """Export a project-local editable draw.io source using the installed CLI.

    After editing a previously registered source, register its new version first
    so old review packets remain bound to the previous diagram digest.
    """
    ws = _workspace()
    root = ws.project_root(project_id)
    source = _safe_path(root, source_path)
    if source.suffix != ".drawio" or output_format not in {"svg", "pdf"}:
        raise ValueError("Expected a .drawio source and svg/pdf output")
    output = source.with_suffix("." + output_format).relative_to(root).as_posix()
    _safe_path(root, output, False)
    existing = _latest_artifacts(project_id).get(source_path)
    if existing:
        _verify_artifact(root, existing)
    kind = _evidence_kind(project_id, [existing] if existing else [])
    from .service import export_diagram
    result = export_diagram(str(source), output_format)
    if result.get("status") != "completed":
        return {**result, "artifact": None}
    editable = ws.register_artifact(project_id, source_path, "diagram_source", kind)
    rendered = ws.register_artifact(project_id, output, "diagram_" + output_format, kind)
    return {**result, "artifact": rendered, "editable_artifact": editable, "evidence_kind": kind}


def _delivery_readiness(project_id: str, artifacts: dict, scientific_artifact_ids: list[str] | None = None) -> list[str]:
    root = _workspace().project_root(project_id)
    plan, missing = _chapter_readiness(project_id)
    kinds = {item["kind"] for item in artifacts.values()}
    if not kinds & CODE_KINDS:
        missing.append("registered experiment source code")
    missing.extend(_result_readiness(project_id, artifacts))
    if not kinds & {"reproduction_readme", "reproduction_instructions"}:
        missing.append("reproduction instructions")
    if not kinds & {"dependency_lock", "environment"}:
        missing.append("dependency/environment specification")
    if "figure_source" not in kinds and not any(Path(p).suffix == ".drawio" for p in artifacts):
        missing.append("editable figure source or reproduction script")
    if not kinds & RENDERED_FIGURE_KINDS:
        missing.append("rendered figure")
    review_input_paths = _build_input_paths(root, plan, artifacts)
    receipt_path = _safe_path(root, "paper/build.json", False)
    pdf = _safe_path(root, "paper/paper.pdf", False)
    if not receipt_path.is_file() or not pdf.is_file():
        missing.append("compiled manuscript and build receipt")
    else:
        receipt = json.loads(receipt_path.read_text())
        expected_inputs = review_input_paths
        if set(receipt.get("inputs", {})) != expected_inputs:
            missing.append("manuscript build is stale: source/figure/style input set changed; recompile")
        for relative, digest in receipt.get("inputs", {}).items():
            path = _safe_path(root, relative, False)
            if not path.is_file() or _sha(path) != digest:
                missing.append("manuscript build is stale: " + relative)
        if _sha(pdf) != receipt.get("pdf_sha256") or not pdf.read_bytes().startswith(b"%PDF-"):
            missing.append("compiled PDF changed")
    manifest_artifact = artifacts.get(RENDER_MANIFEST)
    if not manifest_artifact:
        missing.append("manuscript render manifest is absent; reassemble before review")
    else:
        manifest_path = _verify_artifact(root, manifest_artifact)
        current_inputs = {}
        for relative in review_input_paths:
            source = _safe_path(root, relative, False)
            current_inputs[relative] = _sha(source) if source.is_file() else None
        expected_manifest = _render_manifest_value(project_id, current_inputs, _sha(pdf) if pdf.is_file() else None)
        if json.loads(manifest_path.read_text(encoding="utf-8")) != expected_manifest:
            missing.append("manuscript render manifest is stale; reassemble and review current rendering")
    from .evidence import list_review_packets, review_status
    packets = list_review_packets(project_id)
    for stage in ("experiment", "manuscript"):
        stage_packets = [p for p in packets if p.get("stage") == stage]
        if not stage_packets:
            missing.append(stage + " dual review is absent")
            continue
        declared_evidence = set(scientific_artifact_ids or [])
        required = [a for a in artifacts.values() if a["kind"] in CODE_KINDS | RESULT_KINDS | CONFIRMATION_KINDS
                    or (a["artifact_id"] in declared_evidence and a["kind"] != "source_dependency")] if stage == "experiment" else [
            a for p, a in artifacts.items() if p == RENDER_MANIFEST or (p in review_input_paths and Path(p).suffix.lower() in LOCAL_TEX_SUFFIXES)
        ]
        needed = {a["artifact_id"] for a in required}
        examined, covered = set(), set()
        for packet in stage_packets:  # newest-first: later objections supersede old passes
            relevant = set(packet.get("artifact_ids", [])) & needed - examined
            if not relevant:
                continue
            examined.update(relevant)
            status = review_status(project_id, packet["packet_id"])
            if status.get("status", status.get("state")) == "ready":
                covered.update(relevant)
            else:
                missing.append(stage + " dual review is not ready")
        if needed - covered:
            missing.append(stage + " review does not cover current source/results")
    if any(a.get("evidence_kind") in NONRESEARCH for a in artifacts.values()):
        missing.append("synthetic/toolchain artifacts cannot be a finalized research delivery")
    return list(dict.fromkeys(missing))


def _result_readiness(project_id: str, artifacts: dict) -> list[str]:
    """Require verified machine outputs from distinct main/confirmation jobs.

    Reports can explain the experiment, but registering a report under a result
    kind does not supply the underlying independent evaluation. Job distinctness
    is a necessary provenance check; scientific independence is reviewed against
    the frozen seeds/data and the experiment evidence packet.
    """
    from .evidence import validate_result_artifact
    groups = {"main": RESULT_KINDS,
              "verification": {"verification_results", "replication_results", "independent_check"},
              "ablation": {"ablation_results", "ablation"}}
    jobs = {name: set() for name in groups}
    for artifact in artifacts.values():
        group = next((name for name, kinds in groups.items() if artifact["kind"] in kinds), None)
        if group is None:
            continue
        validation = validate_result_artifact(project_id, artifact["artifact_id"])
        job_ids = validation.get("job_ids") or ([validation["job_id"]] if validation.get("job_id") else [])
        if not validation.get("valid") or not job_ids or any(not isinstance(job_id, str) or not job_id for job_id in job_ids):
            continue
        # A multi-job aggregate remains a main-result table; independent checks
        # and ablations must identify their own executed evaluation job.
        if group != "main" and len(set(job_ids)) != 1:
            continue
        jobs[group].update(job_ids)
    missing = []
    if not jobs["main"]:
        missing.append("main results backed by verified independent-evaluator job artifacts")
    # Preserve exploratory provenance even after a controller removes a main
    # result from the current delivery or re-registers it under another kind.
    exploratory_jobs = set(jobs["main"])
    for previous in _workspace().list_artifacts(project_id):
        if (previous["kind"] in RESULT_KINDS or previous["path"].startswith(("results/search/", "results/baseline/"))):
            if previous.get("source_job_id"):
                exploratory_jobs.add(previous["source_job_id"])
    with connect() as db:
        rows = db.execute("SELECT payload FROM events WHERE project=? AND kind='job_evidence_attached'", (project_id,)).fetchall()
    for row in rows:
        attachment = json.loads(row["payload"])
        if attachment.get("phase") in {"baseline", "search"} and attachment.get("job_id"):
            exploratory_jobs.add(attachment["job_id"])
    independent_checks = jobs["verification"] - exploratory_jobs
    ablation_checks = jobs["ablation"] - exploratory_jobs
    if not independent_checks:
        missing.append("independent verification requires a verified job distinct from main/search jobs")
    if not ablation_checks:
        missing.append("ablation requires a verified job distinct from main/search jobs")
    if independent_checks & ablation_checks:
        missing.append("verification and ablation cannot reuse the same executed job")
    return missing


def _delivery_selection(project_id: str, latest: dict, artifact_ids: list[str]) -> tuple[dict, dict, list[dict]]:
    """Bind an explicit export scope to current paper sources and declared scientific evidence."""
    if not isinstance(artifact_ids, list) or not artifact_ids or any(not isinstance(i, str) or not i for i in artifact_ids) or len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("Select a nonempty list of distinct artifact IDs")
    known = {item["artifact_id"]: item for item in _workspace().list_artifacts(project_id)}
    current = {item["artifact_id"]: item for item in latest.values()}
    for identifier in artifact_ids:
        if identifier not in known:
            raise ValueError("Selected artifact does not belong to this project: " + identifier)
        if identifier not in current:
            raise ValueError("Selected artifact is superseded or not exportable: " + identifier)
    artifacts = {current[i]["path"]: current[i] for i in artifact_ids}
    root = _workspace().project_root(project_id)
    plan, _missing = _chapter_readiness(project_id)
    if not plan:
        raise ValueError("Explicit paper delivery requires a publication plan")
    context_relative = plan.get("context", "paper/context.json")
    context_artifact = latest.get(context_relative)
    if not context_artifact:
        raise ValueError("Explicit delivery requires registered publication context")
    context_path = _verify_artifact(root, context_artifact)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    declared_scope = context.get("delivery_scope") if isinstance(context, dict) else None
    declared = declared_scope.get("evidence_artifact_ids") if isinstance(declared_scope, dict) else None
    if not isinstance(declared, list) or not declared or any(not isinstance(i, str) or not i for i in declared) or len(set(declared)) != len(declared):
        raise ValueError("Set context.delivery_scope.evidence_artifact_ids to the explicit paper evidence IDs before selected delivery")
    if any(identifier not in current for identifier in declared):
        raise ValueError("Declared paper evidence must use current artifacts from this project")
    omitted_evidence = set(declared) - set(artifact_ids)
    if omitted_evidence:
        raise ValueError("Declared paper evidence omitted from delivery: " + ", ".join(sorted(omitted_evidence)))
    required_paths = _build_input_paths(root, plan, artifacts) | {
        "paper/plan.json", context_relative, RENDER_MANIFEST, "paper/build.json", "paper/paper.pdf"}
    build_artifact = latest.get("paper/build.json")
    if build_artifact:
        build = json.loads(_verify_artifact(root, build_artifact).read_text(encoding="utf-8"))
        required_paths.update(build.get("inputs", {}))
    missing_paths = required_paths - set(artifacts)
    if missing_paths:
        raise ValueError("Selected delivery omits required paper/build dependency: " + ", ".join(sorted(missing_paths)))
    selected = set(artifact_ids)
    omitted = [{"artifact_id": item["artifact_id"], "path": item["path"], "sha256": item["sha256"],
                "is_latest": item["artifact_id"] in current,
                "review_coverage": "Not included in this delivery scope; no review pass is asserted"}
               for item in known.values() if item["artifact_id"] not in selected]
    scope = {"mode": "explicit", "selected_artifact_ids": list(artifact_ids),
             "evidence_artifact_ids": declared, "context_artifact_id": context_artifact["artifact_id"],
             "history_policy": "Unselected history remains in the project; this export does not assert its contents were reviewed"}
    return artifacts, scope, omitted


def export_delivery(project_id: str, allow_incomplete: bool = False, artifact_ids: list[str] | None = None) -> dict:
    """Export all current artifacts by default, or an explicitly context-bound paper scope.

    Selected exports require context.delivery_scope.evidence_artifact_ids and all
    paper/build dependencies. Scientific result validation and dual-review gates
    apply to the selected current code/results and declared evidence. Dependency
    identities and omitted history are never represented as fully source-reviewed.
    """
    ws = _workspace()
    root = ws.project_root(project_id)
    artifacts = {p: a for p, a in _latest_artifacts(project_id).items()
                 if a["kind"] not in {"delivery_archive", "delivery_manifest"}}
    scope = {"mode": "all_latest", "selected_artifact_ids": [a["artifact_id"] for a in artifacts.values()]}
    omitted_history = []
    if artifact_ids is not None:
        artifacts, scope, omitted_history = _delivery_selection(project_id, artifacts, artifact_ids)
    inputs = {}
    for relative, artifact in artifacts.items():
        if relative in {"WORKFLOW_RECORD.json", "DELIVERY_MANIFEST.json"}:
            raise ValueError("Reserved delivery filename: " + relative)
        path = _verify_artifact(root, artifact)
        if _sensitive(path):
            raise ValueError("Sensitive file cannot be exported: " + relative)
        inputs[relative] = path
    missing = _delivery_readiness(project_id, artifacts, scope.get("evidence_artifact_ids"))
    if missing and not allow_incomplete:
        return {"status": "incomplete", "missing": missing, "archive": None}
    if not inputs:
        raise ValueError("No registered artifacts to export")
    export_id = uuid.uuid4().hex
    folder = _safe_path(root, "delivery", False)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"project_id": project_id, "created_utc": now(), "status": "incomplete" if missing else "finalized",
                "missing": missing, "evidence_kind": _evidence_kind(project_id, list(artifacts.values())),
                "delivery_scope": scope, "omitted_history": omitted_history,
                "files": [{"path": relative, "sha256": artifacts[relative]["sha256"],
                           "bytes": path.stat().st_size, "artifact_id": artifacts[relative]["artifact_id"],
                           "kind": artifacts[relative]["kind"], "evidence_kind": artifacts[relative].get("evidence_kind", "research")}
                          for relative, path in sorted(inputs.items())]}
    for item in manifest["files"]:
        if item["kind"] == "source_dependency":
            item["review_coverage"] = "Exporter verified file identity/checksum only; full dependency source review is not asserted"
    # The workflow and review records live in SQLite, so include an explicit
    # snapshot rather than silently omitting the reviewers from the delivery.
    from .evidence import list_review_packets, review_status
    packets = list_review_packets(project_id)
    workflow = {"project": ws.project_status(project_id), "delivery_scope": scope,
                "reviews": [review_status(project_id, p["packet_id"]) for p in packets]}
    workflow_bytes = (json.dumps(workflow, ensure_ascii=False, indent=2) + "\n").encode()
    if SECRET_PATTERN.search(workflow_bytes):
        raise ValueError("Sensitive material in workflow record cannot be exported")
    manifest["files"].append({"path": "WORKFLOW_RECORD.json", "sha256": hashlib.sha256(workflow_bytes).hexdigest(),
                              "bytes": len(workflow_bytes), "kind": "workflow_record", "evidence_kind": manifest["evidence_kind"]})
    archive = folder / (export_id + ".zip")
    with tempfile.TemporaryDirectory(prefix=".delivery-", dir=folder) as temporary:
        temporary_zip = Path(temporary) / "bundle.zip"
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for relative, path in sorted(inputs.items()):
                output.write(path, relative)
            output.writestr("WORKFLOW_RECORD.json", workflow_bytes)
            output.writestr("DELIVERY_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(temporary_zip) as verify:
            for item in manifest["files"]:
                if hashlib.sha256(verify.read(item["path"])).hexdigest() != item["sha256"]:
                    raise ValueError("Artifact changed during delivery export: " + item["path"])
        temporary_zip.replace(archive)
    manifest_path = folder / (export_id + ".json")
    _json(manifest_path, manifest)
    record("delivery_exported", {"archive": str(archive), "archive_sha256": _sha(archive),
                                 "manifest": str(manifest_path), "status": manifest["status"], "missing": missing}, project_id)
    return {"status": manifest["status"], "archive": str(archive), "sha256": _sha(archive),
            "manifest": str(manifest_path), "missing": missing}
