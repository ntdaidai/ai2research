"""Register this verified local stdio service without modifying existing MCP entries."""
import json
import os
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
text = target.read_text(encoding="utf-8") if target.exists() else ""
parsed = tomllib.loads(text)
server = os.name == "posix"
name = "ai2research" if server else "research_harness"
entry = {
    "command": str(root / ("ai2research" if server else ".venv/Scripts/python.exe")),
    "args": ["serve"] if server else [str(root / "tools/harness_mcp.py")],
    "cwd": str(root),
    "env_vars": ["DASHSCOPE_API_KEY", "RESEARCH_STATE_DIR", "DRAWIO_EXE"],
    "startup_timeout_sec": 45,
    "tool_timeout_sec": 120,
}
existing = parsed.get("mcp_servers", {}).get(name)
if existing:
    if existing != entry:
        raise SystemExit(f"{name} already exists with a different configuration; left unchanged")
    print(f"{name} already registered")
else:
    block = f"\n[mcp_servers.{name}]\n" + "\n".join(k + " = " + json.dumps(v, ensure_ascii=True) for k, v in entry.items()) + "\n"
    tomllib.loads(text + block)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + block, encoding="utf-8")
    print(f"Registered {name}; existing MCP entries preserved. Refresh Codex MCP connection to discover tools.")
