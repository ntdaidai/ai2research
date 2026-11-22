import argparse
import asyncio
import inspect
import json
import sys
from . import service


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "start":
        return _start(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "showcase":
        from .showcase import main as showcase
        return showcase(sys.argv[2:])
    parser = argparse.ArgumentParser(description="ai2research researcher CLI and MCP server")
    parser.add_argument("tool", nargs="?", default="doctor")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON arguments or @path/to/arguments.json")
    args = parser.parse_args()
    if args.tool == "serve":
        from .server import main as serve
        serve()
        return
    allowed = {name for name in dir(service) if callable(getattr(service, name)) and not name.startswith("_")
               and getattr(getattr(service, name), "__module__", None) == service.__name__}
    if args.tool not in allowed:
        parser.error("Unknown tool; available: " + ", ".join(sorted(allowed)))
    payload = args.arguments
    if payload.startswith("@"):
        from pathlib import Path
        payload = Path(payload[1:]).read_text(encoding="utf-8-sig")
    try:
        value = getattr(service, args.tool)(**json.loads(payload))
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


def _start(argv):
    """A paper-first CLI shorthand; reasoning and delegation stay with the live controller."""
    import hashlib
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Create parallel screening tasks from paper links, arXiv IDs or local PDF paths")
    parser.add_argument("papers", nargs="*", help="Paper references; or a single @JSON file containing seed_papers")
    parser.add_argument("--project-id", "--project", dest="project_id")
    parser.add_argument("--title", default="")
    parser.add_argument("--domain", help="Start from a research domain instead of supplied papers")
    parser.add_argument("--requirements", default="")
    parser.add_argument("--as-of", default="2026-11-18", help="Client calendar date for the publication window")
    parser.add_argument("--toolchain-test", action="store_true", help="Label this workspace as infrastructure acceptance, not research")
    args = parser.parse_args(argv)
    papers = args.papers
    project_id = args.project_id
    title = args.title
    try:
        if args.domain:
            if papers or args.toolchain_test:
                raise ValueError("Domain discovery accepts no paper seeds or toolchain-test label")
            project_id = project_id or "domain-" + hashlib.sha256(json.dumps([args.domain, args.requirements, args.as_of], ensure_ascii=False).encode()).hexdigest()[:12]
            from .discovery import start_from_request
            result = start_from_request(project_id, args.domain, args.requirements, as_of=args.as_of)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.requirements:
            raise ValueError("--requirements requires --domain")
        if len(papers) == 1 and papers[0].startswith("@"):
            source = json.loads(Path(papers[0][1:]).read_text(encoding="utf-8-sig"))
            if isinstance(source, dict):
                papers = source["seed_papers"]
                project_id = project_id or source.get("project_id")
                title = title or source.get("title", "")
            else:
                papers = source
        if not isinstance(papers, list) or not papers or any(not isinstance(p, str) or not p.strip() for p in papers):
            raise ValueError("Supply nonempty paper references")
        project_id = project_id or "papers-" + hashlib.sha256(json.dumps(papers, ensure_ascii=False).encode()).hexdigest()[:12]
        result = service.start_project(project_id, papers, title, "toolchain_test" if args.toolchain_test else "research")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
