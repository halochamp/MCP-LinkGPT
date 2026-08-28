from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import server  # noqa: E402


class _CapturingContext:
	def __init__(self) -> None:
		self.events: list[tuple[float, float | None, str | None]] = []

	async def report_progress(self, progress: float, total: float | None, message: str | None) -> None:
		self.events.append((progress, total, message))


class _ProgressReportingBridge:
	async def ask(self, *_: object, progress: object = None, **__: object) -> dict[str, object]:
		assert callable(progress)
		await progress(35, "Question sent. Waiting for ChatGPT to start responding.")
		await progress(65, "ChatGPT is generating its response.\n\nLatest visible response tail:\npartial context")
		await progress(100, "ChatGPT has finished and the final response is ready.")
		return {
			"ok": True,
			"status": "completed",
			"message": "ChatGPT has finished and the final response is ready.",
			"response": "final answer",
		}


class ServerToolTests(unittest.IsolatedAsyncioTestCase):
	async def test_ask_forwards_safe_progress_and_completed_result(self) -> None:
		context = _CapturingContext()
		with patch.object(server, "bridge", _ProgressReportingBridge()):
			result = await server.chatgpt_ask("review this", ctx=context)  # type: ignore[arg-type]

		self.assertEqual(result["status"], "completed")
		self.assertEqual(result["message"], "ChatGPT has finished and the final response is ready.")
		self.assertEqual(
			context.events,
			[
				(35, 100, "Question sent. Waiting for ChatGPT to start responding."),
				(65, 100, "ChatGPT is generating its response.\n\nLatest visible response tail:\npartial context"),
				(100, 100, "ChatGPT has finished and the final response is ready."),
			],
		)


if __name__ == "__main__":
	unittest.main()
