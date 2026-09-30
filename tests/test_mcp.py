"""End-to-end: spawn `python -m objection mcp` over stdio, as OpenCode/Cline do, and call the tools."""

import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CONFIG = """
models:
  - {id: a, model: mock/a}
  - {id: b, model: mock/b}
  - {id: c, model: mock/c}
storage: {path: "%s"}
"""


def test_mcp_stdio_end_to_end(tmp_path):
    cfg = tmp_path / "objection.yaml"
    cfg.write_text(CONFIG % (tmp_path / "runs.sqlite"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "objection", "mcp"],
                                   env={**os.environ, "OBJECTION_CONFIG": str(cfg)})

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"council_ask", "council_review", "council_models"} <= tools
                models = await session.call_tool("council_models", {})
                review = await session.call_tool("council_review", {"target": "+x = call()", "kind": "diff"})
                again = await session.call_tool("council_review", {"target": "+x = call()", "kind": "diff"})
                return models, review, again

    models, review, again = asyncio.run(asyncio.wait_for(go(), 60))
    data = lambda r: r.structured_content or json.loads(r.content[0].text)  # noqa: E731
    assert [m["id"] for m in data(models)["models"]] == ["a", "b", "c"]
    rv = data(review)
    assert rv["verdict"] == "fail" and rv["findings"][0]["status"] == "confirmed"
    assert rv["rejected_findings"] >= 1
    assert data(again)["cached"] is True
