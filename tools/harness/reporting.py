"""Export measured project records as a table, plot and inspectable Markdown report."""
import csv
import hashlib
import json
import math
import uuid
from pathlib import Path
from .state import connect, local_dir, record

REPORT_COLUMNS = ("job_id", "state", "metric", "value", "evaluation_valid", "approval_id", "proposal_id",
                  "script_sha256", "metrics_authority", "peak_gpu_memory_mb", "mlflow_run_id", "local_path")


def export_project_report(project_id, metric, direction="minimize"):
    from .recovery import list_jobs
    from .service import _known_job
    if direction not in ("minimize", "maximize"):
        raise ValueError("Invalid metric direction")
    jobs = list_jobs(project_id, 100)["jobs"]
    with connect() as db:
        raw = db.execute("SELECT payload FROM events WHERE project=? AND kind='job_collected' ORDER BY rowid", (project_id,)).fetchall()
    collected = {item["job_id"]: item for item in (json.loads(r[0]) for r in raw)}
    rows = []
    for item in reversed(jobs):
        known = _known_job(item["job_id"])
        saved = collected.get(item["job_id"])
        status = saved["status"] if saved else {"state": item["state"]}
        value = saved.get("metrics", {}).get(metric) if saved else None
        valid = saved is not None and status["state"] == "completed" and not saved.get("metrics_error") and isinstance(value, (int, float)) and math.isfinite(value)
        rows.append({"job_id": item["job_id"], "state": status["state"], "metric": metric, "value": value if valid else "",
                     "evaluation_valid": valid, "approval_id": known.get("approval_id", ""), "proposal_id": known.get("proposal_id", ""),
                     "script_sha256": known.get("spec", {}).get("script_sha256", ""),
                     "metrics_authority": status.get("metrics_authority", ""), "peak_gpu_memory_mb": status.get("peak_gpu_memory_mb", ""),
                     "mlflow_run_id": saved["mlflow_run_id"] if saved else "", "local_path": saved["local_path"] if saved else ""})
    valid_rows = [r for r in rows if r["evaluation_valid"]]
    synthetic = any(_known_job(r["job_id"]).get("spec", {}).get("kind", "").startswith("synthetic") for r in rows)
    scopes = {r["approval_id"] for r in valid_rows}
    best = (min if direction == "minimize" else max)(valid_rows, key=lambda r: r["value"]) if valid_rows and len(scopes) == 1 else None
    folder = local_dir() / "reports" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    with (folder / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    graph = None
    if valid_rows:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        fig, ax = plt.subplots(figsize=(7.2, 4.2), layout="constrained")
        bars = ax.bar(["Trial " + str(i + 1) for i in range(len(valid_rows))], [r["value"] for r in valid_rows], color="#2563eb", width=0.5)
        ax.set_ylabel(metric)
        ax.set_title("Synthetic workflow acceptance" if synthetic else "Measured trial results")
        ax.bar_label(bars, labels=[format(r["value"], ".6g") for r in valid_rows], padding=4)
        ax.set_ylim(top=max(1, max(r["value"] for r in valid_rows)) * 1.2)
        ax.spines[["top", "right"]].set_visible(False)
        fig.savefig(folder / "metric.png", dpi=160)
        plt.close(fig)
        graph = str(folder / "metric.png")
    lines = ["# Project run report", "", "Project: `" + project_id + "`", "", "Evidence: " + ("synthetic toolchain acceptance; no research claims" if synthetic else "recorded experiment outputs; scientific claims require review"),
             "", "Metric: `" + metric + "` (" + direction + ").", "", "| Trial | State | Value | GPU memory MiB | Evaluator |", "|---|---|---:|---:|---|"]
    for i, row in enumerate(rows):
        lines.append("| " + str(i + 1) + " / `" + row["job_id"][:8] + "` | " + str(row["state"]) + " | " + str(row["value"]) + " | " + str(row["peak_gpu_memory_mb"]) + " | " + row["metrics_authority"] + " |")
    if graph:
        lines.extend(["", "![Measured metrics](metric.png)"])
    if best:
        lines.extend(["", "Best valid recorded trial within this approval scope: `" + best["job_id"] + "`."])
    lines.extend(["", "The CSV and JSON retain full job/run IDs and source checksums. Failed or uncollected trials remain listed and are excluded from metric selection."])
    report = folder / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "report-data.json").write_text(json.dumps({"project_id": project_id, "rows": rows, "best": best}, ensure_ascii=False, indent=2), encoding="utf-8")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    return record("project_report_created", {"project_id": project_id, "report": str(report), "csv": str(folder / "results.csv"),
                  "graph": graph, "source_rows": len(rows), "valid_trials": len(valid_rows), "best_job_id": best["job_id"] if best else None,
                  "file_sha256": hashes}, project_id)
