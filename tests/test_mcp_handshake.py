from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_DIR = Path(__file__).resolve().parents[1]


class MCPHandshakeTests(unittest.IsolatedAsyncioTestCase):
	async def test_server_lists_expected_tools(self) -> None:
		env = dict(os.environ)
		env["BROWSER_USE_CONFIG_DIR"] = "/tmp/mcp-linkgpt-test-browseruse-config"
		parameters = StdioServerParameters(
			command=sys.executable,
			args=[str(PROJECT_DIR / "server.py")],
			cwd=str(PROJECT_DIR),
			env=env,
		)
		async with stdio_client(parameters) as (read_stream, write_stream):
			async with ClientSession(read_stream, write_stream) as session:
				initialized = await session.initialize()
				self.assertEqual(initialized.serverInfo.name, "MCP-LinkGPT")
				result = await session.list_tools()
				self.assertEqual(
					{tool.name for tool in result.tools},
					{
						"chatgpt_ask",
						"chatgpt_close",
						"chatgpt_last_response",
						"chatgpt_new_chat",
						"chatgpt_status",
					},
				)
				close_result = await session.call_tool("chatgpt_close", {})
				self.assertFalse(close_result.isError)
				self.assertEqual(
					close_result.structuredContent,
					{"ok": True, "status": "closed"},
				)
				ask_tool = next(tool for tool in result.tools if tool.name == "chatgpt_ask")
				status_tool = next(tool for tool in result.tools if tool.name == "chatgpt_status")
				close_tool = next(tool for tool in result.tools if tool.name == "chatgpt_close")
				new_chat_tool = next(tool for tool in result.tools if tool.name == "chatgpt_new_chat")
				last_response_tool = next(tool for tool in result.tools if tool.name == "chatgpt_last_response")
				descriptions = {
					tool.name: " ".join((tool.description or "").lower().split())
					for tool in result.tools
				}
				ask_description = descriptions["chatgpt_ask"]
				self.assertEqual(ask_tool.inputSchema["properties"]["timeout_seconds"]["default"], 600)
				self.assertIn("do not send another prompt", ask_description)
				self.assertIn("progress notifications", ask_description)
				self.assertIn("progress values do not decrease", ask_description)
				self.assertIn('status="completed"', ask_description)
				self.assertIn("chatgpt_last_response", ask_description)
				self.assertIn("tails are context", ask_description)
				self.assertIn("explicit user direction", ask_description)
				self.assertIn("host mcp tool timeout must be no shorter", ask_description)
				self.assertIn("bridge owns the bounded", descriptions[status_tool.name])
				self.assertIn("never returns transient ``loading``", descriptions[status_tool.name])
				self.assertIn("continue only at", descriptions[status_tool.name])
				self.assertIn("never close while chatgpt_ask is waiting", descriptions[close_tool.name])
				self.assertIn("never use a new chat to hide", descriptions[new_chat_tool.name])
				self.assertIn("after chatgpt_ask explicitly times out", descriptions[last_response_tool.name])
				self.assertIn("ambiguous non-timeout error", descriptions[last_response_tool.name])
				for description in descriptions.values():
					self.assertNotIn("luna", description)


if __name__ == "__main__":
	unittest.main()
