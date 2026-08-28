#!/usr/bin/env python3
"""MCP entry point for the MCP-LinkGPT browser bridge."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "critical")
os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")

from mcp.server.fastmcp import Context, FastMCP

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
	"""Open ChatGPT and wait for one final readiness result.

	Use once before chatgpt_ask. Wait for this call; the bridge owns the bounded
	readiness loop and never returns transient ``loading``. Continue only at
	``ready``; ``login_required`` or ``challenge`` needs user action. Do not call
	any browser tool while chatgpt_ask is waiting.
	"""

	try:
		return await bridge.status()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_close() -> dict[str, object]:
	"""Close ChatGPT after tool work or an already returned failure.

	Never close while chatgpt_ask is waiting. Finish any allowed timeout recovery
	first; cancellation after submission is not a rollback.
	"""

	try:
		await bridge.close()
		return {"ok": True, "status": "closed"}
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_new_chat() -> dict[str, object]:
	"""Open a fresh conversation when no chatgpt_ask call is running.

	For a normal independent request, prefer ``chatgpt_ask(new_chat=True)``.
	Never use a new chat to hide or recover an ambiguous post-submit error.
	"""

	try:
		return await bridge.new_chat()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_last_response() -> dict[str, object]:
	"""Read the latest response after chatgpt_ask explicitly times out.

	Do not resend the prompt. ``in_progress`` is provisional; accept only
	``completed``. Do not use this after an ambiguous non-timeout error because
	the latest response may belong to another turn.
	"""

	try:
		return await bridge.last_response()
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


@mcp.tool()
async def chatgpt_ask(
	prompt: str,
	new_chat: bool = True,
	timeout_seconds: int = 600,
	ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, object]:
	"""Send one prompt and wait for its confirmed final response.

	Use after chatgpt_status returns ``ready``. Do not send another prompt or call
	another browser tool while waiting. Progress notifications and response tails
	are context, not final advice; progress values do not decrease when ChatGPT
	temporarily hides an already-started response turn. Accept only ``ok=true`` with
	``status="completed"``. After a timeout, recover with chatgpt_last_response
	without resending. After any
	ambiguous non-timeout error, discard partial output and do not retry; a new
	attempt requires explicit user direction.

	Args:
		prompt: Text to send. Content is not written to logs.
		new_chat: Start from a fresh conversation before sending.
		timeout_seconds: Response timeout from 10 to 900 seconds. The host MCP tool
			timeout must be no shorter; local connector work may take several minutes.
	"""

	try:
		async def report(progress: float, message: str) -> None:
			if ctx is not None:
				await ctx.report_progress(progress, 100, message)

		return await bridge.ask(
			prompt,
			new_chat=new_chat,
			timeout_seconds=timeout_seconds,
			progress=report,
		)
	except ChatGPTWebError as exc:
		return _error_result(exc)
	except Exception:
		return _internal_error_result()


if __name__ == "__main__":
	mcp.run(transport="stdio")
