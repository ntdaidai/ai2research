#!/usr/bin/env python3
"""Small, dependency-free control plane for the research workspace."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "project.json"
STAGES = ROOT / "workflow" / "stages.json"
RUNS = ROOT / "runs"


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 格式错误：{path}:{exc.lineno}: {exc.msg}") from exc


def missing_config(config: dict) -> list[str]:
    required = ["project_name", "research_question", "field", "baseline_repository"]
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    for key in ("seed_papers", "datasets"):
        if not isinstance(config.get(key), list) or not config[key]:
            missing.append(key)
    metric = config.get("primary_metric", {})
    if not isinstance(metric, dict) or not str(metric.get("name", "")).strip():
        missing.append("primary_metric.name")
    if not isinstance(metric, dict) or metric.get("direction") not in ("maximize", "minimize"):
        missing.append("primary_metric.direction")
    budget = config.get("budget", {})
    if not isinstance(budget, dict) or not isinstance(budget.get("max_idea_trials"), int) or budget["max_idea_trials"] <= 0:
        missing.append("budget.max_idea_trials (> 0)")
    return missing


def doctor(_args):
    print(f"工作目录：{ROOT}")
    print(f"Python：{platform.python_version()} {'OK' if sys.version_info >= (3, 11) else '需要 3.11+'}")
    for tool in ("git", "docker", "uv", "nvidia-smi"):
        print(f"{tool}：{shutil.which(tool) or '未找到（服务器接入时再安装/检查）'}")
    missing = missing_config(read_json(CONFIG))
    if missing:
        print("课题配置待填写：" + ", ".join(missing))
    else:
        print("课题配置：可创建运行")
    print("此检查不验证服务器、模型凭证或实验真实性。")


def new_run(_args):
    config = read_json(CONFIG)
    missing = missing_config(config)
    if missing:
        raise SystemExit("先填写 project.json：" + ", ".join(missing))
    stages = read_json(STAGES)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS / run_id
    if run_dir.exists():
        raise SystemExit("同一秒内已有运行，请稍后重试。")
    run_dir.mkdir(parents=True)
    (run_dir / "project.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "stages.json").write_text(json.dumps(stages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "created_utc": datetime.now(timezone.utc).isoformat(), "state": "created"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for stage in stages:
        (run_dir / stage["id"]).mkdir()
    print(f"已创建：{run_dir}")
    print("阶段目录为空；请运行真实研究任务并保存证据。")


def evidence_report(run_dir: Path) -> list[tuple[str, int, int, list[str]]]:
    stages = read_json(run_dir / "stages.json")
    report = []
    for stage in stages:
        evidence = stage["evidence"]
        absent = [item for item in evidence if not (run_dir / item).is_file() or (run_dir / item).stat().st_size == 0]
        report.append((stage["name"], len(evidence) - len(absent), len(evidence), absent))
    return report


def resolve_run(run_id: str) -> Path:
    run_dir = (RUNS / run_id).resolve()
    if run_dir.parent != RUNS.resolve() or not run_dir.is_dir():
        raise SystemExit(f"未找到运行：{run_id}")
    return run_dir


def check(args):
    run_dir = resolve_run(args.run_id)
    for name, found, total, absent in evidence_report(run_dir):
        print(f"{name}: {found}/{total}")
        if args.verbose:
            for item in absent:
                print(f"  缺少或为空：{item}")
    print("文件检查只确认产物存在；数字复现、引用与方法一致性仍需独立审计。")


def status(_args):
    run_dirs = sorted((p for p in RUNS.iterdir() if p.is_dir() and (p / "run.json").is_file()), reverse=True)
    if not run_dirs:
        print("尚无正式运行。填写 project.json 后执行：python tools/research.py new-run")
        return
    for run_dir in run_dirs[:10]:
        report = evidence_report(run_dir)
        completed = sum(found == total for _, found, total, _ in report)
        print(f"{run_dir.name}: {completed}/{len(report)} 个阶段的要求文件齐全")
    if len(run_dirs) > 10:
        print(f"还有 {len(run_dirs) - 10} 次较早的运行。")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="研究工作台的本地控制工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查本地工具和课题配置").set_defaults(func=doctor)
    sub.add_parser("new-run", help="按当前课题配置创建独立运行").set_defaults(func=new_run)
    sub.add_parser("status", help="查看最近运行").set_defaults(func=status)
    check_parser = sub.add_parser("check", help="检查一次运行的证据文件")
    check_parser.add_argument("run_id")
    check_parser.add_argument("--verbose", action="store_true")
    check_parser.set_defaults(func=check)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
