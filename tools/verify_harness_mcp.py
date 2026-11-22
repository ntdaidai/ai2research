"""Verify actual stdio MCP transport and cross-process shared state, no paid/GPU calls."""
import asyncio
import json
import sys
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from harness.state import ROOT, events, local_dir


async def main():
    transport = StdioTransport(command=sys.executable, args=[str(ROOT / "tools/harness_mcp.py")], cwd=str(ROOT))
    async with Client(transport, timeout=30) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        assert "review_topic" in names and "get_job_status" in names
        await client.call_tool("record_decision", {"project_id": "acceptance-test", "text": "stdio MCP write/read acceptance"})
        state = await client.call_tool("get_project_state", {"project_id": "acceptance-test"})
        assert state.data["events"][0]["text"] == "stdio MCP write/read acceptance"
    assert events("acceptance-test")[0]["text"] == "stdio MCP write/read acceptance"
    result = {"status": "passed", "transport": "stdio", "tools": names, "cross_process_state": "passed"}
    (local_dir() / "mcp-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


asyncio.run(main())
