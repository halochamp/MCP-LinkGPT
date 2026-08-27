#!/usr/bin/env python3
"""MCP entry point for the MCP-LinkGPT browser bridge."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "critical")
os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")

from mcp.server.fastmcp import FastMCP

from chatgpt_web import ChatGPTWebBridge, ChatGPTWebError


bridge = ChatGPTWebBridge()


@asynccontextmanager
async def app_lifespan(_: FastMCP) -> AsyncIterator[dict[str, object]]:
	try:
		yield {}
	finally:
		with suppress(Exception):
			await bridge.close()


mcp = FastMCP(
	"MCP-LinkGPT",
	instructions=(
		"Use these tools only when the user explicitly wants Codex to consult ChatGPT Web. "
		"The first use opens a dedicated visible Chrome profile for manual sign-in."
	),
	lifespan=app_lifespan,
)


def _error_result(exc: Exception) -> dict[str, object]:
	return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


def _internal_error_result() -> dict[str, object]:
	return {"ok": False, "error": "Unexpected internal bridge failure.", "error_type": "InternalBridgeError"}


@mcp.tool()
async def chatgpt_status() -> dict[str, object]:
	"""Open the dedicated browser if needed and report whether ChatGPT Web is ready."""

	try:
		return await bridge.status()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_new_chat() -> dict[str, object]:
	"""Navigate the dedicated browser profile to a fresh ChatGPT conversation."""

	try:
		return await bridge.new_chat()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_last_response() -> dict[str, object]:
	"""Return the latest assistant response without sending another prompt."""

	try:
		return await bridge.last_response()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_ask(prompt: str, new_chat: bool = True, timeout_seconds: int = 600) -> dict[str, object]:
	"""Send one prompt to ChatGPT Web and return its completed response.

	Args:
		prompt: Text to send. Content is not written to logs.
		new_chat: Start from a fresh conversation before sending.
		timeout_seconds: Response timeout from 10 to 900 seconds. Local connector work may take several minutes.
	"""

	try:
		return await bridge.ask(prompt, new_chat=new_chat, timeout_seconds=timeout_seconds)
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


if __name__ == "__main__":
	mcp.run(transport="stdio")
