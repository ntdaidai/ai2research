"""Check a fresh installation without research jobs, paid calls or SSH."""
import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


async def check_mcp(directory):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    original = os.environ.get("RESEARCH_STATE_DIR")
    os.environ["RESEARCH_STATE_DIR"] = str(directory)
    try:
        transport = StdioTransport(command=sys.executable, args=[str(ROOT / "tools/harness_mcp.py")], cwd=str(ROOT))
        async with Client(transport, timeout=45) as client:
            names = [t.name for t in await client.list_tools()]
            assert {"list_projects", "start_from_request", "research_workflow"}.issubset(names)
            await client.call_tool("record_decision", {"project_id": "install-check", "text": "isolated installation check"})
            state = await client.call_tool("get_project_state", {"project_id": "install-check"})
            assert any(item.get("text") == "isolated installation check" for item in state.data["events"])
        return {"tools": len(names), "stdio": "passed", "isolated_state_roundtrip": "passed"}
    finally:
        if original is None:
            os.environ.pop("RESEARCH_STATE_DIR", None)
        else:
            os.environ["RESEARCH_STATE_DIR"] = original


def verify(*, full=False):
    from harness.showcase import make_server
    report = {"platform": sys.platform, "python": sys.version.split()[0], "mode": "full" if full else "workbench", "checks": []}
    with tempfile.TemporaryDirectory(prefix="ai2research-install-") as temporary:
        directory = Path(temporary) / "empty-state"
        server = make_server("127.0.0.1", 0, directory=directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            for name in ("/", "/style.css", "/app.js", "/catalog.js", "/favicon.svg"):
                with urllib.request.urlopen(base + name, timeout=10) as response:
                    assert response.status == 200 and response.read(), name
            with urllib.request.urlopen(base + "/api/snapshot", timeout=10) as response:
                data = json.load(response)
            assert data["projects"] == [] and data["project"] is None
            assert len(data["tools"]["items"]) == 14
            assert not directory.exists(), "Viewing the workbench initialized research state"
            report["checks"].extend(["five_frontend_assets", "empty_project_snapshot", "read_only_workbench"])
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        if full:
            for name in ("fastmcp", "litellm", "optuna", "mlflow-skinny", "matplotlib"):
                importlib.metadata.version(name)
            env = dict(os.environ, RESEARCH_STATE_DIR=str(Path(temporary) / "cli-state"), PYTHONUTF8="1")
            result = subprocess.run([sys.executable, str(ROOT / "tools/harness_cli.py"), "list_projects"],
                                    env=env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=45, check=True)
            assert isinstance(json.loads(result.stdout), dict)
            report["mcp"] = asyncio.run(check_mcp(Path(temporary) / "mcp-state"))
            report["checks"].extend(["core_dependencies", "cli_process", "mcp_roundtrip"])
    report["status"] = "passed"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also check installed core dependencies, CLI and actual stdio MCP")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/install-check.json")
    args = parser.parse_args()
    try:
        result = verify(full=args.full)
    except Exception as exc:
        parser.exit(1, f"Installation check failed: {type(exc).__name__}: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
