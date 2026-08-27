from __future__ import annotations

import sys
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_DIR = Path(__file__).resolve().parents[1]


class MCPHandshakeTests(unittest.IsolatedAsyncioTestCase):
	async def test_server_lists_expected_tools(self) -> None:
		parameters = StdioServerParameters(
			command=sys.executable,
			args=[str(PROJECT_DIR / "server.py")],
			cwd=str(PROJECT_DIR),
		)
		async with stdio_client(parameters) as (read_stream, write_stream):
			async with ClientSession(read_stream, write_stream) as session:
				initialized = await session.initialize()
				self.assertEqual(initialized.serverInfo.name, "MCP-LinkGPT")
				result = await session.list_tools()

		self.assertEqual(
			{tool.name for tool in result.tools},
			{"chatgpt_ask", "chatgpt_last_response", "chatgpt_new_chat", "chatgpt_status"},
		)
		ask_tool = next(tool for tool in result.tools if tool.name == "chatgpt_ask")
		self.assertEqual(ask_tool.inputSchema["properties"]["timeout_seconds"]["default"], 600)


if __name__ == "__main__":
	unittest.main()
