from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from chatgpt_web import (  # noqa: E402
	ChallengeDetectedError,
	ChatGPTWebBridge,
	ChatGPTWebError,
	CookieSafeBrowserSession,
	LoginRequiredError,
	ReadinessTimeoutError,
	REPOSITORY_ROOT,
)


async def no_sleep(_: float) -> None:
	return None


class ScriptedBridge(ChatGPTWebBridge):
	def __init__(self, states: list[dict[str, Any]]) -> None:
		super().__init__(profile_dir=Path("/tmp/mcp-linkgpt-test-profile"), sleep=no_sleep, poll_interval=0)
		self.states = list(states)
		self.navigations = 0
		self.inserted: list[str] = []
		self.submitted: list[str] = []
		self.submits = 0

	async def _ensure_session(self) -> object:  # type: ignore[override]
		return object()

	async def _navigate(self, url: str = "https://chatgpt.com/") -> None:
		self.navigations += 1

	async def _page_state(self) -> dict[str, Any]:
		if len(self.states) > 1:
			return self.states.pop(0)
		return self.states[0]

	async def _focus_and_clear_composer(self) -> None:
		return None

	async def _insert_text(self, text: str) -> None:
		self.inserted.append(text)

	async def _submit(self, prompt: str, timeout_seconds: float = 8.0) -> None:
		self.submitted.append(prompt)
		self.submits += 1


def state(**overrides: Any) -> dict[str, Any]:
	base = {
		"url": "https://chatgpt.com/",
		"title": "ChatGPT",
		"composer_ready": True,
		"assistant_count": 0,
		"latest_text": "",
		"user_count": 0,
		"latest_user_text": "",
		"streaming": False,
		"challenge": False,
		"login_required": False,
		"document_token": "document-1",
	}
	base.update(overrides)
	return base


class ChatGPTWebBridgeTests(unittest.IsolatedAsyncioTestCase):
	async def test_page_state_does_not_scan_conversation_text_for_challenge_phrases(self) -> None:
		class CapturingBridge(ChatGPTWebBridge):
			def __init__(self) -> None:
				super().__init__(profile_dir=Path("/tmp/mcp-linkgpt-capture-profile"))
				self.expression = ""

			async def _evaluate(self, expression: str) -> dict[str, Any]:
				self.expression = expression
				return state()

		bridge = CapturingBridge()
		await bridge._page_state()

		self.assertNotIn("bodyText.includes('verify you are human')", bridge.expression)
		self.assertNotIn("bodyText.includes('ตรวจสอบว่าคุณเป็นมนุษย์')", bridge.expression)
		self.assertIn('iframe[src*="challenge"]', bridge.expression)

	async def test_page_state_excludes_collapsible_user_turn_toggle_text(self) -> None:
		class CapturingBridge(ChatGPTWebBridge):
			def __init__(self) -> None:
				super().__init__(profile_dir=Path("/tmp/mcp-linkgpt-capture-profile"))
				self.expression = ""

			async def _evaluate(self, expression: str) -> dict[str, Any]:
				self.expression = expression
				return state()

		bridge = CapturingBridge()
		await bridge._page_state()

		content_selector = '[data-testid="collapsible-user-message-content"]'
		self.assertIn(content_selector, bridge.expression)
		self.assertLess(
			bridge.expression.index(content_selector),
			bridge.expression.index("latestUserContent || latestUserNode"),
		)

	async def test_existing_assistant_text_change_is_not_a_new_turn(self) -> None:
		bridge = ScriptedBridge(
			[
				state(assistant_count=1, latest_text="old partial"),
				state(assistant_count=1, latest_text="old final", user_count=1, latest_user_text="next prompt"),
				state(assistant_count=1, latest_text="old final", user_count=1, latest_user_text="next prompt"),
				state(assistant_count=1, latest_text="old final", user_count=1, latest_user_text="next prompt"),
				state(assistant_count=2, latest_text="new response", user_count=1, latest_user_text="next prompt"),
				state(assistant_count=2, latest_text="new response", user_count=1, latest_user_text="next prompt"),
				state(assistant_count=2, latest_text="new response", user_count=1, latest_user_text="next prompt"),
			]
		)

		result = await bridge.ask("next prompt", new_chat=False, timeout_seconds=10)

		self.assertEqual(result["response"], "new response")

	async def test_last_response_reads_without_sending(self) -> None:
		bridge = ScriptedBridge([state(assistant_count=1, latest_text="late answer")])

		result = await bridge.last_response()

		self.assertEqual(result["response"], "late answer")
		self.assertEqual(bridge.inserted, [])
		self.assertEqual(bridge.submits, 0)

	async def test_failed_start_kills_partial_session(self) -> None:
		killed = False

		class BrokenSession:
			async def start(self) -> None:
				raise RuntimeError("start failed")

			async def kill(self) -> None:
				nonlocal killed
				killed = True

		def factory(**_: Any) -> Any:
			return BrokenSession()

		with tempfile.TemporaryDirectory() as temp_dir:
			bridge = ChatGPTWebBridge(profile_dir=Path(temp_dir) / "profile", session_factory=factory)
			with self.assertRaises(ChatGPTWebError):
				await bridge._ensure_session()

		self.assertTrue(killed)

	async def test_cancel_during_session_start_retains_session_and_profile_lock(self) -> None:
		start_entered = asyncio.Event()

		class StartBlockedSession:
			async def start(self) -> None:
				start_entered.set()
				await asyncio.Future()

			async def kill(self) -> None:
				return None

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir, session_factory=lambda **_: StartBlockedSession())
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			task = asyncio.create_task(first._ensure_session())
			await start_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertIsNotNone(first._session)
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_cancel_during_failed_start_cleanup_retains_session_and_profile_lock(self) -> None:
		kill_entered = asyncio.Event()
		kill_calls = 0

		class CleanupBlockedSession:
			async def start(self) -> None:
				raise RuntimeError("start failed")

			async def kill(self) -> None:
				nonlocal kill_calls
				kill_calls += 1
				if kill_calls == 1:
					kill_entered.set()
					await asyncio.Future()

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir, session_factory=lambda **_: CleanupBlockedSession())
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			task = asyncio.create_task(first._ensure_session())
			await kill_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertIsNotNone(first._session)
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				self.assertEqual(kill_calls, 2)
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_cancel_during_invalidate_keeps_profile_lock(self) -> None:
		kill_entered = asyncio.Event()
		kill_calls = 0

		class InvalidateBlockedSession:
			async def kill(self) -> None:
				nonlocal kill_calls
				kill_calls += 1
				if kill_calls == 1:
					kill_entered.set()
					await asyncio.Future()

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			session = InvalidateBlockedSession()
			first._session = session  # type: ignore[assignment]
			first._acquire_profile_lock()
			task = asyncio.create_task(first._invalidate_session(session))
			await kill_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertIs(first._session, session)
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				self.assertEqual(kill_calls, 2)
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_cancel_during_close_keeps_profile_lock(self) -> None:
		kill_entered = asyncio.Event()
		kill_calls = 0

		class CloseBlockedSession:
			async def kill(self) -> None:
				nonlocal kill_calls
				kill_calls += 1
				if kill_calls == 1:
					kill_entered.set()
					await asyncio.Future()

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			session = CloseBlockedSession()
			first._session = session  # type: ignore[assignment]
			first._acquire_profile_lock()
			task = asyncio.create_task(first.close())
			await kill_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertIs(first._session, session)
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				self.assertEqual(kill_calls, 2)
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_profile_lock_rejects_second_bridge(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			first._acquire_profile_lock()
			try:
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
			finally:
				first._release_profile_lock()

	async def test_profile_dir_rejects_repository_root_and_descendant(self) -> None:
		with self.assertRaisesRegex(ChatGPTWebError, "outside this repository"):
			ChatGPTWebBridge(profile_dir=REPOSITORY_ROOT)
		with self.assertRaisesRegex(ChatGPTWebError, "outside this repository"):
			ChatGPTWebBridge(profile_dir=REPOSITORY_ROOT / "nested-profile")

	async def test_profile_dir_rejects_symlink_into_repository(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			alias = Path(temp_dir) / "repo-alias"
			alias.symlink_to(REPOSITORY_ROOT, target_is_directory=True)
			with self.assertRaisesRegex(ChatGPTWebError, "outside this repository"):
				ChatGPTWebBridge(profile_dir=alias / "profile")

	async def test_profile_aliases_share_canonical_lock_identity(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			physical_profile = Path(temp_dir) / "physical-profile"
			physical_profile.mkdir()
			alias = Path(temp_dir) / "profile-alias"
			alias.symlink_to(physical_profile, target_is_directory=True)
			first = ChatGPTWebBridge(profile_dir=physical_profile)
			second = ChatGPTWebBridge(profile_dir=alias)
			self.assertEqual(first.profile_dir, second.profile_dir)
			first._acquire_profile_lock()
			try:
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
			finally:
				first._release_profile_lock()

	async def test_unknown_profile_ownership_marker_blocks_a_new_bridge(self) -> None:
		class Session:
			async def start(self) -> None:
				return None

			async def kill(self) -> None:
				return None

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir, session_factory=lambda **_: Session())
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			await first._ensure_session()
			marker = first._ownership_marker_path
			self.assertIsNotNone(marker)
			self.assertTrue(marker.is_file())

			# Simulate the first process exiting while browser ownership is uncertain:
			# the OS releases the flock, but the persistent marker remains.
			first._release_profile_lock()
			try:
				with self.assertRaisesRegex(ChatGPTWebError, "uncertain ownership"):
					await second._ensure_session()
				self.assertIsNone(second._profile_lock_handle)
			finally:
				await first.close()
			self.assertFalse(marker.exists())

	async def test_profile_path_is_rechecked_before_use(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			bridge = ChatGPTWebBridge(profile_dir=profile_dir)
			profile_dir.symlink_to(REPOSITORY_ROOT, target_is_directory=True)
			with self.assertRaisesRegex(ChatGPTWebError, "outside this repository"):
				await bridge._ensure_session()

	async def test_profile_lock_rejects_symlink(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			lock_target = Path(temp_dir) / "lock-target"
			lock_target.touch()
			lock_path = profile_dir.parent / ".profile.lock"
			lock_path.symlink_to(lock_target)
			bridge = ChatGPTWebBridge(profile_dir=profile_dir)
			with self.assertRaisesRegex(ChatGPTWebError, "opened safely"):
				bridge._acquire_profile_lock()

	async def test_browser_exception_is_wrapped_in_stable_error(self) -> None:
		class Runtime:
			async def evaluate(self, **_: Any) -> Any:
				raise ConnectionError("secret low-level detail")

		class Send:
			def __init__(self) -> None:
				self.Runtime = Runtime()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self) -> None:
				self.cdp_client = CDPClient()
				self.session_id = "test-session"
				self.target_id = "test-target"

		class Session:
			async def get_or_create_cdp_session(self, **_: Any) -> Any:
				return CDPSession()

		bridge = ChatGPTWebBridge(profile_dir=Path("/tmp/mcp-linkgpt-error-profile"))
		bridge._session = Session()  # type: ignore[assignment]

		with self.assertRaisesRegex(ChatGPTWebError, "connection was lost") as raised:
			await bridge._evaluate("1 + 1")
		self.assertNotIn("secret low-level detail", str(raised.exception))

	async def test_connection_loss_invalidates_session_for_next_call(self) -> None:
		factory_calls = 0
		dead_kills = 0

		class DeadSession:
			async def start(self) -> None:
				return None

			async def get_or_create_cdp_session(self, **_: Any) -> Any:
				raise ConnectionError("simulated disconnect")

			async def kill(self) -> None:
				nonlocal dead_kills
				dead_kills += 1

		class Runtime:
			async def evaluate(self, **_: Any) -> Any:
				return {"result": {"value": "healthy"}}

		class Send:
			def __init__(self) -> None:
				self.Runtime = Runtime()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self) -> None:
				self.cdp_client = CDPClient()
				self.session_id = "healthy-session"
				self.target_id = "healthy-target"

		class HealthySession:
			async def start(self) -> None:
				return None

			async def get_or_create_cdp_session(self, **_: Any) -> Any:
				return CDPSession()

			async def kill(self) -> None:
				return None

		def factory(**_: Any) -> Any:
			nonlocal factory_calls
			factory_calls += 1
			return DeadSession() if factory_calls == 1 else HealthySession()

		with tempfile.TemporaryDirectory() as temp_dir:
			bridge = ChatGPTWebBridge(profile_dir=Path(temp_dir) / "profile", session_factory=factory)
			with self.assertRaisesRegex(ChatGPTWebError, "connection was lost"):
				await bridge._evaluate("1 + 1")
			self.assertEqual(await bridge._evaluate("1 + 1"), "healthy")
			self.assertEqual(factory_calls, 2)
			self.assertEqual(dead_kills, 1)
			await bridge.close()

	async def test_cdp_target_is_bound_for_the_duration_of_an_operation(self) -> None:
		requested_targets: list[str | None] = []

		class Runtime:
			async def evaluate(self, **_: Any) -> Any:
				return {"result": {"value": "ok"}}

		class Send:
			def __init__(self) -> None:
				self.Runtime = Runtime()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self) -> None:
				self.cdp_client = CDPClient()
				self.session_id = "session-a"
				self.target_id = "target-a"

		class Session:
			async def get_or_create_cdp_session(self, *, target_id: str | None, focus: bool) -> Any:
				requested_targets.append(target_id)
				self.assert_focus_argument(focus)
				return CDPSession()

			def assert_focus_argument(self, focus: bool) -> None:
				assert focus is False

		bridge = ChatGPTWebBridge(profile_dir=Path("/tmp/mcp-linkgpt-target-test"))
		bridge._session = Session()  # type: ignore[assignment]
		with bridge._operation_scope():
			self.assertEqual(await bridge._evaluate("1 + 1"), "ok")
			self.assertEqual(await bridge._evaluate("2 + 2"), "ok")
		self.assertEqual(requested_targets, [None, "target-a"])

	async def test_cdp_target_drift_is_rejected(self) -> None:
		requested_targets: list[str | None] = []

		class Runtime:
			async def evaluate(self, **_: Any) -> Any:
				return {"result": {"value": "ok"}}

		class Send:
			def __init__(self) -> None:
				self.Runtime = Runtime()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self, target_id: str) -> None:
				self.cdp_client = CDPClient()
				self.session_id = f"session-{target_id}"
				self.target_id = target_id

		class Session:
			async def get_or_create_cdp_session(self, *, target_id: str | None, focus: bool) -> Any:
				requested_targets.append(target_id)
				assert focus is False
				return CDPSession("target-a" if target_id is None else "target-b")

		bridge = ChatGPTWebBridge(profile_dir=Path("/tmp/mcp-linkgpt-target-drift-test"))
		bridge._session = Session()  # type: ignore[assignment]
		with bridge._operation_scope():
			self.assertEqual(await bridge._evaluate("1 + 1"), "ok")
			with self.assertRaisesRegex(ChatGPTWebError, "target changed"):
				await bridge._evaluate("2 + 2")
		self.assertEqual(requested_targets, [None, "target-a"])

	async def test_cancel_during_runtime_evaluate_blocks_reconnect(self) -> None:
		evaluate_entered = asyncio.Event()

		class Runtime:
			async def evaluate(self, **_: Any) -> Any:
				evaluate_entered.set()
				await asyncio.Future()

		class Send:
			def __init__(self) -> None:
				self.Runtime = Runtime()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self) -> None:
				self.cdp_client = CDPClient()
				self.session_id = "mutation-session"
				self.target_id = "mutation-target"

		class Session:
			async def get_or_create_cdp_session(self, **_: Any) -> Any:
				return CDPSession()

			async def kill(self) -> None:
				return None

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			session = Session()
			first._session = session  # type: ignore[assignment]
			first._acquire_profile_lock()

			async def run_evaluate() -> Any:
				with first._operation_scope():
					return await first._evaluate("1 + 1")

			task = asyncio.create_task(run_evaluate())
			await evaluate_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_cancel_during_navigation_blocks_reconnect(self) -> None:
		navigate_entered = asyncio.Event()

		class Page:
			async def navigate(self, **_: Any) -> Any:
				navigate_entered.set()
				await asyncio.Future()

		class Send:
			def __init__(self) -> None:
				self.Page = Page()

		class CDPClient:
			def __init__(self) -> None:
				self.send = Send()

		class CDPSession:
			def __init__(self) -> None:
				self.cdp_client = CDPClient()
				self.session_id = "navigation-session"
				self.target_id = "navigation-target"

		class Session:
			async def get_or_create_cdp_session(self, **_: Any) -> Any:
				return CDPSession()

			async def kill(self) -> None:
				return None

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			session = Session()
			first._session = session  # type: ignore[assignment]
			first._acquire_profile_lock()

			async def run_navigation() -> None:
				with first._operation_scope():
					await first._navigate()

			task = asyncio.create_task(run_navigation())
			await navigate_entered.wait()
			task.cancel()
			with self.assertRaises(asyncio.CancelledError):
				await task
			try:
				self.assertTrue(first._reconnect_blocked)
				with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
					second._acquire_profile_lock()
				await first.close()
				second._acquire_profile_lock()
			finally:
				first._release_profile_lock()
				second._release_profile_lock()

	async def test_guest_composer_still_requires_login(self) -> None:
		bridge = ScriptedBridge([state(composer_ready=True, login_required=True)])
		with self.assertRaises(LoginRequiredError):
			await bridge._wait_for_composer()

	async def test_auth_openai_url_requires_login_without_button_text(self) -> None:
		with self.assertRaises(LoginRequiredError):
			ChatGPTWebBridge._raise_for_page_state(
				state(url="https://auth.openai.com/authorize", login_required=False)
			)

	async def test_status_reports_auth_openai_url_as_login_required(self) -> None:
		bridge = ScriptedBridge([state(url="https://auth.openai.com/authorize", login_required=False)])
		result = await bridge.status()
		self.assertFalse(result["ok"])
		self.assertEqual(result["status"], "login_required")
		self.assertEqual(bridge.navigations, 0)

	async def test_status_waits_through_transient_loading_until_ready(self) -> None:
		bridge = ScriptedBridge(
			[
				state(composer_ready=False),
				state(composer_ready=False),
				state(composer_ready=True),
			]
		)

		result = await bridge.status(timeout_seconds=1)

		self.assertTrue(result["ok"])
		self.assertEqual(result["status"], "ready")

	async def test_status_readiness_timeout_closes_before_returning_failure(self) -> None:
		class TimeoutBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state(composer_ready=False)])
				self.closed_after_timeout = False

			async def _close_session_unlocked(self) -> None:
				self.closed_after_timeout = True

		bridge = TimeoutBridge()

		with self.assertRaises(ReadinessTimeoutError):
			await bridge.status(timeout_seconds=0)

		self.assertTrue(bridge.closed_after_timeout)

	async def test_status_readiness_deadline_includes_browser_startup(self) -> None:
		class SlowStartBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state()])
				self.closed_after_timeout = False

			async def _ensure_session(self) -> object:  # type: ignore[override]
				await asyncio.sleep(0.05)
				return object()

			async def _close_session_unlocked(self) -> None:
				self.closed_after_timeout = True

		bridge = SlowStartBridge()
		started = asyncio.get_running_loop().time()

		with self.assertRaises(ReadinessTimeoutError):
			await bridge.status(timeout_seconds=0.005)

		elapsed = asyncio.get_running_loop().time() - started
		self.assertLess(elapsed, 0.04)
		self.assertTrue(bridge.closed_after_timeout)

	async def test_readiness_timeout_preserves_cause_when_cleanup_fails(self) -> None:
		class CleanupFailureBridge(ScriptedBridge):
			async def _wait_for_readiness(
				self,
				timeout_seconds: float = 30.0,
				*,
				deadline: float | None = None,
			) -> tuple[str, dict[str, Any]]:
				raise ReadinessTimeoutError("readiness expired")

			async def _close_session_unlocked(self) -> None:
				raise ChatGPTWebError("kill failed")

		bridge = CleanupFailureBridge([state()])

		with self.assertRaises(ReadinessTimeoutError) as raised:
			await bridge.status(timeout_seconds=1)

		self.assertIn("readiness expired", str(raised.exception))
		self.assertIn("shutdown also failed", str(raised.exception).lower())
		self.assertIsInstance(raised.exception.__cause__, ChatGPTWebError)

	async def test_status_challenge_is_terminal_and_does_not_close_browser(self) -> None:
		class ChallengeBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state(challenge=True, composer_ready=False)])
				self.closed = False

			async def _close_session_unlocked(self) -> None:
				self.closed = True

		bridge = ChallengeBridge()
		result = await bridge.status()

		self.assertFalse(result["ok"])
		self.assertEqual(result["status"], "challenge")
		self.assertFalse(bridge.closed)

	async def test_page_state_uses_structural_login_scope_and_document_identity(self) -> None:
		class CapturingBridge(ChatGPTWebBridge):
			def __init__(self) -> None:
				super().__init__(profile_dir=Path("/tmp/mcp-linkgpt-page-state-test"))
				self.expression = ""

			async def _evaluate(self, expression: str) -> dict[str, Any]:
				self.expression = expression
				return state()

		bridge = CapturingBridge()
		await bridge._page_state()
		self.assertNotIn("document.querySelectorAll('a, button')", bridge.expression)
		self.assertIn("isConversationContent", bridge.expression)
		self.assertIn("isAuthHost", bridge.expression)
		self.assertIn("document_token", bridge.expression)

	async def test_mutation_guards_include_full_origin_and_page_identity(self) -> None:
		expressions: list[str] = []

		class CapturingBridge(ChatGPTWebBridge):
			def __init__(self) -> None:
				super().__init__(profile_dir=Path("/tmp/mcp-linkgpt-mutation-test"), sleep=no_sleep)

			async def _assert_interactive_page(
				self, *, expected_identity: tuple[str, str | None] | None = None
			) -> dict[str, Any]:
				return state()

			async def _evaluate(self, expression: str) -> Any:
				expressions.append(expression)
				return True

		bridge = CapturingBridge()
		bridge._operation_page_identity = ("https://chatgpt.com/", "document-1")
		await bridge._focus_and_clear_composer()
		await bridge._insert_text("safe prompt")
		await bridge._submit("safe prompt", timeout_seconds=10)
		self.assertEqual(len(expressions), 3)
		for expression in expressions:
			self.assertIn("currentUrl.port", expression)
			self.assertIn("currentUrl.username", expression)
			self.assertIn("location.href !==", expression)
			self.assertIn("performance.timeOrigin", expression)
		self.assertNotIn("Input.insertText", "\n".join(expressions))
		self.assertIn("expectedPrompt", expressions[-1])
		self.assertIn("composerValue", expressions[-1])
		self.assertIn("composer.childNodes", expressions[-1])
		self.assertIn(".join('\\n')", expressions[-1])
		self.assertNotIn("replace(/\\s+/g", expressions[-1])

	async def test_same_target_page_identity_change_fails_closed(self) -> None:
		class IdentityChangingBridge(ScriptedBridge):
			async def _page_state(self) -> dict[str, Any]:
				return state(document_token="document-2")

		bridge = IdentityChangingBridge([state()])
		bridge._operation_page_identity = ("https://chatgpt.com/", "document-1")
		with self.assertRaisesRegex(ChatGPTWebError, "page changed"):
			await bridge._assert_interactive_page(expected_identity=bridge._operation_page_identity)

	async def test_ask_rejects_off_domain_page_before_insert(self) -> None:
		bridge = ScriptedBridge([state(url="https://chatgpt.com.evil.example/")])
		with self.assertRaisesRegex(ChatGPTWebError, "outside the approved ChatGPT domain"):
			await bridge.ask("should not leave this page", new_chat=False, timeout_seconds=10)
		self.assertEqual(bridge.inserted, [])
		self.assertEqual(bridge.submits, 0)

	async def test_status_rejects_hostile_chatgpt_prefix(self) -> None:
		bridge = ScriptedBridge([state(url="https://chatgpt.com.evil.example/")])
		with self.assertRaisesRegex(ChatGPTWebError, "outside the approved ChatGPT domain"):
			await bridge.status()
		self.assertEqual(bridge.navigations, 1)

	async def test_last_response_rejects_off_domain_page(self) -> None:
		bridge = ScriptedBridge(
			[state(url="https://chatgpt.com.evil.example/", assistant_count=1, latest_text="secret")]
		)
		with self.assertRaisesRegex(ChatGPTWebError, "outside the approved ChatGPT domain"):
			await bridge.last_response()

	async def test_wait_until_idle_stops_on_login_expiry(self) -> None:
		bridge = ScriptedBridge(
			[
				state(streaming=True),
				state(composer_ready=False, login_required=True, streaming=False),
			]
		)
		with self.assertRaises(LoginRequiredError):
			await bridge._wait_until_idle(timeout_seconds=10)

	async def test_ask_stops_on_login_expiry_after_submit(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(composer_ready=False, login_required=True),
			]
		)
		with self.assertRaises(LoginRequiredError):
			await bridge.ask("prompt", timeout_seconds=10)

	async def test_ask_readiness_timeout_closes_before_prompt_submission(self) -> None:
		class TimeoutBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state(composer_ready=False)])
				self.closed_after_timeout = False

			async def _wait_for_composer(
				self,
				timeout_seconds: float = 30.0,
				*,
				deadline: float | None = None,
			) -> dict[str, Any]:
				raise ReadinessTimeoutError("not ready")

			async def _close_session_unlocked(self) -> None:
				self.closed_after_timeout = True

		bridge = TimeoutBridge()

		with self.assertRaises(ReadinessTimeoutError):
			await bridge.ask("prompt", timeout_seconds=10)

		self.assertTrue(bridge.closed_after_timeout)
		self.assertEqual(bridge.inserted, [])
		self.assertEqual(bridge.submits, 0)

	async def test_new_chat_readiness_timeout_closes_before_returning_failure(self) -> None:
		class TimeoutBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state(composer_ready=False)])
				self.closed_after_timeout = False

			async def _wait_for_composer(
				self,
				timeout_seconds: float = 30.0,
				*,
				deadline: float | None = None,
			) -> dict[str, Any]:
				raise ReadinessTimeoutError("not ready")

			async def _close_session_unlocked(self) -> None:
				self.closed_after_timeout = True

		bridge = TimeoutBridge()

		with self.assertRaises(ReadinessTimeoutError):
			await bridge.new_chat()

		self.assertTrue(bridge.closed_after_timeout)

	async def test_ask_readiness_deadline_includes_browser_startup(self) -> None:
		class SlowStartBridge(ScriptedBridge):
			def __init__(self) -> None:
				super().__init__([state()])
				self.closed_after_timeout = False

			async def _ensure_session(self) -> object:  # type: ignore[override]
				await asyncio.sleep(0.05)
				return object()

			async def _close_session_unlocked(self) -> None:
				self.closed_after_timeout = True

		bridge = SlowStartBridge()
		with patch("chatgpt_web.DEFAULT_READINESS_TIMEOUT_SECONDS", 0.005):
			with self.assertRaises(ReadinessTimeoutError):
				await bridge.ask("prompt", timeout_seconds=10)

		self.assertTrue(bridge.closed_after_timeout)
		self.assertEqual(bridge.inserted, [])
		self.assertEqual(bridge.submits, 0)

	async def test_close_keeps_profile_lock_when_browser_kill_fails(self) -> None:
		class Session:
			async def kill(self) -> None:
				raise RuntimeError("kill failed")

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir)
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			first._session = Session()  # type: ignore[assignment]
			first._acquire_profile_lock()
			with self.assertRaisesRegex(ChatGPTWebError, "did not shut down cleanly"):
				await first.close()
			with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
				second._acquire_profile_lock()
			with self.assertRaisesRegex(ChatGPTWebError, "did not shut down cleanly"):
				await first.close()
			with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
				second._acquire_profile_lock()
			first._release_profile_lock()

	async def test_failed_start_keeps_profile_lock_when_partial_kill_fails(self) -> None:
		class BrokenSession:
			async def start(self) -> None:
				raise RuntimeError("start failed")

			async def kill(self) -> None:
				raise RuntimeError("kill failed")

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			first = ChatGPTWebBridge(profile_dir=profile_dir, session_factory=lambda **_: BrokenSession())
			second = ChatGPTWebBridge(profile_dir=profile_dir)
			with self.assertRaises(ChatGPTWebError):
				await first._ensure_session()
			with self.assertRaisesRegex(ChatGPTWebError, "already in use"):
				second._acquire_profile_lock()
			first._release_profile_lock()

	async def test_session_factory_keeps_persistent_profile_path(self) -> None:
		captured: dict[str, Any] = {}

		class FakeSession:
			async def start(self) -> None:
				return None

			async def kill(self) -> None:
				return None

		def factory(**kwargs: Any) -> Any:
			captured.update(kwargs)
			return FakeSession()

		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "persistent-profile"
			bridge = ChatGPTWebBridge(profile_dir=profile_dir, session_factory=factory)
			await bridge._ensure_session()
			await bridge.close()

		self.assertEqual(captured["user_data_dir"], bridge.profile_dir)
		self.assertEqual(captured["window_size"], {"width": 760, "height": 560})
		self.assertEqual(captured["window_position"], {"width": 24, "height": 60})
		self.assertIsNone(captured["storage_state"])
		self.assertFalse(captured["enable_default_extensions"])
		self.assertNotIn("executable_path", captured)

	async def test_cookie_safe_session_does_not_attach_storage_watchdog(self) -> None:
		with tempfile.TemporaryDirectory() as temp_dir:
			profile_dir = Path(temp_dir) / "profile"
			session = CookieSafeBrowserSession(
				user_data_dir=profile_dir,
				storage_state=None,
				enable_default_extensions=False,
				captcha_solver=False,
				headless=False,
			)
			await session.attach_all_watchdogs()

			self.assertIsNone(session._storage_state_watchdog)
			self.assertEqual(session.browser_profile.user_data_dir, profile_dir.resolve())

	async def test_window_options_can_be_overridden(self) -> None:
		captured: dict[str, Any] = {}

		class FakeSession:
			async def start(self) -> None:
				return None

			async def kill(self) -> None:
				return None

		def factory(**kwargs: Any) -> Any:
			captured.update(kwargs)
			return FakeSession()

		with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
			"os.environ",
			{
				"CODEX_CHATGPT_WINDOW_WIDTH": "900",
				"CODEX_CHATGPT_WINDOW_HEIGHT": "700",
				"CODEX_CHATGPT_WINDOW_X": "100",
				"CODEX_CHATGPT_WINDOW_Y": "120",
			},
		):
			bridge = ChatGPTWebBridge(profile_dir=Path(temp_dir) / "profile", session_factory=factory)
			await bridge._ensure_session()
			await bridge.close()

		self.assertEqual(captured["window_size"], {"width": 900, "height": 700})
		self.assertEqual(captured["window_position"], {"width": 100, "height": 120})

	async def test_ask_returns_stable_completed_response(self) -> None:
		progress_events: list[tuple[float, str]] = []

		async def report(progress: float, message: str) -> None:
			progress_events.append((progress, message))

		bridge = ScriptedBridge(
			[
				state(),
				state(
					assistant_count=1,
					latest_text="กำลังตอบ",
					streaming=True,
					user_count=1,
					latest_user_text="ทดสอบ",
				),
				state(assistant_count=1, latest_text="คำตอบสุดท้าย", user_count=1, latest_user_text="ทดสอบ"),
				state(assistant_count=1, latest_text="คำตอบสุดท้าย", user_count=1, latest_user_text="ทดสอบ"),
				state(assistant_count=1, latest_text="คำตอบสุดท้าย", user_count=1, latest_user_text="ทดสอบ"),
			]
		)

		result = await bridge.ask("ทดสอบ", timeout_seconds=10, progress=report)

		self.assertTrue(result["ok"])
		self.assertEqual(result["status"], "completed")
		self.assertEqual(result["message"], "ChatGPT has finished and the final response is ready.")
		self.assertEqual(result["response"], "คำตอบสุดท้าย")
		self.assertEqual(bridge.inserted, ["ทดสอบ"])
		self.assertEqual(bridge.submits, 1)
		self.assertEqual(bridge.navigations, 1)
		self.assertIn((35, "Question sent. Waiting for ChatGPT to start responding."), progress_events)
		self.assertIn(
			(65, "ChatGPT is generating its response.\n\nLatest visible response tail:\nกำลังตอบ"),
			progress_events,
		)
		self.assertEqual(progress_events[-1], (100, "ChatGPT has finished and the final response is ready."))

	async def test_progress_does_not_regress_when_response_turn_is_temporarily_hidden(self) -> None:
		progress_events: list[tuple[float, str]] = []

		async def report(progress: float, message: str) -> None:
			progress_events.append((progress, message))

		bridge = ScriptedBridge(
			[
				state(),
				state(
					assistant_count=1,
					latest_text="Thinking",
					streaming=True,
					user_count=1,
					latest_user_text="prompt",
				),
				state(),
				state(user_count=1, latest_user_text="prompt"),
				state(assistant_count=1, latest_text="answer", user_count=1, latest_user_text="prompt"),
				state(assistant_count=1, latest_text="answer", user_count=1, latest_user_text="prompt"),
				state(assistant_count=1, latest_text="answer", user_count=1, latest_user_text="prompt"),
			]
		)

		result = await bridge.ask("prompt", new_chat=False, timeout_seconds=10, progress=report)

		self.assertEqual(result["response"], "answer")
		progress_values = [value for value, _ in progress_events]
		self.assertEqual(progress_values, sorted(progress_values))
		self.assertIn(
			(65, "ChatGPT started responding; waiting for the page to expose the response again."),
			progress_events,
		)

	def test_progress_response_tail_is_bounded_to_recent_content(self) -> None:
		response = "a" * 20 + "last context"
		with patch("chatgpt_web.MAX_PROGRESS_TAIL_CHARS", 12):
			tail = ChatGPTWebBridge._progress_response_tail(response)

		self.assertEqual(tail, "…last context")

	async def test_last_response_labels_completed_and_in_progress(self) -> None:
		completed = ScriptedBridge([state(assistant_count=1, latest_text="final")])
		in_progress = ScriptedBridge([state(assistant_count=1, latest_text="partial", streaming=True)])

		completed_result = await completed.last_response()
		in_progress_result = await in_progress.last_response()

		self.assertEqual(completed_result["status"], "completed")
		self.assertEqual(completed_result["message"], "ChatGPT has finished and the final response is ready.")
		self.assertEqual(in_progress_result["status"], "in_progress")
		self.assertEqual(in_progress_result["message"], "ChatGPT is still generating a response.")

	async def test_new_chat_transition_is_pinned_to_submitted_prompt(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(
					url="https://chatgpt.com/c/WEB:temporary",
					document_token="document-1",
					user_count=1,
					latest_user_text="prompt",
					assistant_count=1,
					latest_text="partial",
					streaming=True,
				),
				state(
					url="https://chatgpt.com/c/new-conversation",
					document_token="document-2",
					user_count=1,
					latest_user_text="prompt",
					assistant_count=1,
					latest_text="answer",
				),
				state(
					url="https://chatgpt.com/c/new-conversation",
					document_token="document-2",
					user_count=1,
					latest_user_text="prompt",
					assistant_count=1,
					latest_text="answer",
				),
				state(
					url="https://chatgpt.com/c/new-conversation",
					document_token="document-2",
					user_count=1,
					latest_user_text="prompt",
					assistant_count=1,
					latest_text="answer",
				),
			]
		)

		result = await bridge.ask("prompt", timeout_seconds=10)

		self.assertEqual(result["response"], "answer")
		self.assertEqual(result["url"], "https://chatgpt.com/c/new-conversation")

	async def test_post_submit_conversation_change_fails_closed(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(
					url="https://chatgpt.com/c/other-conversation",
					document_token="document-2",
					user_count=1,
					latest_user_text="different prompt",
					assistant_count=1,
					latest_text="unrelated answer",
				),
			]
		)

		with self.assertRaisesRegex(ChatGPTWebError, "conversation changed"):
			await bridge.ask("prompt", new_chat=False, timeout_seconds=10)

	async def test_same_conversation_manual_interference_fails_closed(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text="manual prompt",
					assistant_count=1,
					latest_text="manual answer",
				),
			]
		)

		with self.assertRaisesRegex(ChatGPTWebError, r"user turn changed.*reason=text"):
			await bridge.ask(
				"bridge prompt",
				new_chat=False,
				timeout_seconds=10,
				strict_user_turn_text=True,
			)

	async def test_multiple_new_user_turns_report_count_mismatch(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=2,
					latest_user_text="bridge prompt",
					assistant_count=1,
					latest_text="unowned answer",
				),
			]
		)

		with self.assertRaisesRegex(ChatGPTWebError, r"user turn changed.*reason=count"):
			await bridge.ask("bridge prompt", new_chat=False, timeout_seconds=10)

	async def test_rejects_empty_prompt(self) -> None:
		bridge = ScriptedBridge([state()])
		with self.assertRaisesRegex(ChatGPTWebError, "non-empty"):
			await bridge.ask("   ")

	async def test_stops_on_human_verification(self) -> None:
		bridge = ScriptedBridge([state(challenge=True, composer_ready=False)])
		with self.assertRaises(ChallengeDetectedError):
			await bridge.ask("hello", timeout_seconds=10)
		self.assertEqual(bridge.inserted, [])

	async def test_status_does_not_return_message_content(self) -> None:
		bridge = ScriptedBridge([state(assistant_count=2, latest_text="secret response")])
		result = await bridge.status()
		self.assertEqual(result["status"], "ready")
		self.assertNotIn("latest_text", result)
		self.assertNotIn("secret response", repr(result))

	def test_markdown_normalization_only_applies_confirmed_rendering_changes(self) -> None:
		self.assertEqual(
			ChatGPTWebBridge._normalize_markdown_text("Review `_profile_lock` now."),
			"Review _profile_lock now.",
		)
		self.assertEqual(
			ChatGPTWebBridge._normalize_markdown_text("C# a*b x > y # literal"),
			"C# a*b x > y # literal",
		)
		self.assertEqual(
			ChatGPTWebBridge._normalize_markdown_text("unmatched `backtick"),
			"unmatched `backtick",
		)
		fenced = "before\n```python\nx = 1\nprint(x)\n```\nafter"
		rendered = "before\n\npython\nx = 1\nprint(x)\n\nafter"
		self.assertEqual(ChatGPTWebBridge._normalize_markdown_text(fenced), rendered)
		self.assertEqual(
			ChatGPTWebBridge._normalize_markdown_text("before\n\n```python\nx = 1\n```"),
			"before\n\n\npython\nx = 1",
		)
		self.assertEqual(
			ChatGPTWebBridge._normalize_markdown_text("before\n```python\nx = 1\nafter"),
			"before\n```python\nx = 1\nafter",
		)
		self.assertNotEqual(
			ChatGPTWebBridge._normalize_markdown_text(fenced),
			ChatGPTWebBridge._normalize_markdown_text(rendered.replace("x = 1", "x = 2")),
		)

	def test_text_normalization_preserves_semantic_whitespace(self) -> None:
		self.assertEqual(
			ChatGPTWebBridge._normalized_text("line 1\r\n\xa0 \xa0 line 2\rline 3"),
			"line 1\n    line 2\nline 3",
		)
		self.assertNotEqual(
			ChatGPTWebBridge._normalized_text("if authorized:\n    delete_files()"),
			ChatGPTWebBridge._normalized_text("if authorized:\ndelete_files()"),
		)
		self.assertNotEqual(
			ChatGPTWebBridge._normalized_text('value = "a  b"'),
			ChatGPTWebBridge._normalized_text('value = "a b"'),
		)
		self.assertNotEqual(
			ChatGPTWebBridge._normalized_text("ALLOW\nDENY"),
			ChatGPTWebBridge._normalized_text("ALLOW DENY"),
		)

	def test_mismatch_metadata_is_structural_bounded_and_content_free(self) -> None:
		expected = "private alpha\n    secret value\n" + "x\n" * 20
		observed = "private alpha\nsecret value\n" + "x\n" * 20

		metadata = ChatGPTWebBridge._mismatch_metadata(expected, observed, stage="markdown")

		self.assertIn("stage=markdown", metadata)
		self.assertIn("first_diff_line=1", metadata)
		self.assertIn("first_diff_column=0", metadata)
		self.assertIn("expected_class=SPACE", metadata)
		self.assertIn("observed_class=ASCII_ALNUM", metadata)
		self.assertIn("expected_whitespace_run=4", metadata)
		self.assertIn("remaining_length_delta=-4", metadata)
		self.assertIn(",...", metadata)
		self.assertNotIn("private", metadata)
		self.assertNotIn("secret", metadata)

	async def test_user_turn_rejects_semantic_whitespace_changes(self) -> None:
		cases = [
			("if authorized:\n    delete_files()", "if authorized:\ndelete_files()"),
			('value = "a  b"', 'value = "a b"'),
			("ALLOW\nDENY", "ALLOW DENY"),
		]
		for raw_prompt, rendered_text in cases:
			with self.subTest(raw_prompt=raw_prompt):
				bridge = ScriptedBridge(
					[
						state(),
						state(
							user_count=1,
							latest_user_text=rendered_text,
							assistant_count=1,
							latest_text="unowned",
						),
					]
				)
				with self.assertRaisesRegex(ChatGPTWebError, r"user turn changed.*reason=text") as raised:
					await bridge.ask(
						raw_prompt,
						new_chat=False,
						timeout_seconds=10,
						strict_user_turn_text=True,
					)
				self.assertIn("stage=markdown", str(raised.exception))
				self.assertNotIn("delete_files", str(raised.exception))
				self.assertNotIn("ALLOW", str(raised.exception))

	async def test_user_turn_accepts_inline_code_rendered_text(self) -> None:
		raw_prompt = "Review `_profile_lock` and `chatgpt_status()` with high priority."
		rendered_text = "Review _profile_lock and chatgpt_status() with high priority."
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text=rendered_text,
					assistant_count=1,
					latest_text="reviewed",
					stop_visible=False,
				),
			]
		)
		result = await bridge.ask(raw_prompt, new_chat=False, timeout_seconds=10)
		self.assertEqual(result["response"], "reviewed")

	async def test_user_turn_accepts_fenced_code_rendered_text(self) -> None:
		raw_prompt = "before\n```python\nx = 1\nprint(x)\n```\nafter"
		rendered_text = "before\n\npython\nx = 1\nprint(x)\n\nafter"
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text=rendered_text,
					assistant_count=1,
					latest_text="reviewed",
					stop_visible=False,
				),
			]
		)
		result = await bridge.ask(raw_prompt, new_chat=False, timeout_seconds=10)
		self.assertEqual(result["response"], "reviewed")

	async def test_user_turn_returns_warning_for_default_structural_fallback(self) -> None:
		raw_prompt = "before\n```python\nx = 1\n```\nafter"
		rendered_text = "before\n\npython\n\nx = 1\n\nafter"
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text=rendered_text,
					assistant_count=1,
					latest_text="usable with warning",
					stop_visible=False,
				),
			]
		)

		result = await bridge.ask(raw_prompt, new_chat=False, timeout_seconds=10)

		self.assertEqual(result["response"], "usable with warning")
		self.assertEqual(result["correlation_status"], "rendering_fallback")
		self.assertIn("structural ownership fallback", result["correlation_warning"])
		self.assertNotIn("x = 1", result["correlation_warning"])

	async def test_default_structural_fallback_returns_semantic_mismatch_with_warning(self) -> None:
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text="different rendered text",
					assistant_count=1,
					latest_text="usable but lower-confidence",
					stop_visible=False,
				),
			]
		)

		result = await bridge.ask("original prompt", new_chat=False, timeout_seconds=10)

		self.assertEqual(result["status"], "completed")
		self.assertEqual(result["correlation_status"], "rendering_fallback")
		self.assertIn("rendered text correlation differed", result["correlation_warning"])
		self.assertNotIn("original prompt", result["correlation_warning"])
		self.assertNotIn("different rendered text", result["correlation_warning"])

	async def test_user_turn_rejects_high_similarity_semantic_change(self) -> None:
		raw_prompt = ("Review this safety policy carefully. " * 80) + "DO NOT DELETE FILES."
		rendered_text = ("Review this safety policy carefully. " * 80) + "DELETE FILES."
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text=rendered_text,
					assistant_count=1,
					latest_text="unowned",
					stop_visible=False,
				),
			]
		)
		with self.assertRaisesRegex(ChatGPTWebError, r"user turn changed.*reason=text"):
			await bridge.ask(raw_prompt, new_chat=False, timeout_seconds=10, strict_user_turn_text=True)

	async def test_user_turn_rejects_unrelated_text(self) -> None:
		raw_prompt = "A" * 100
		rendered_text = "B" * 100
		bridge = ScriptedBridge(
			[
				state(),
				state(
					user_count=1,
					latest_user_text=rendered_text,
					assistant_count=1,
					latest_text="unrelated",
				),
			]
		)
		with self.assertRaisesRegex(ChatGPTWebError, r"user turn changed.*reason=text"):
			await bridge.ask(raw_prompt, new_chat=False, timeout_seconds=10, strict_user_turn_text=True)


if __name__ == "__main__":
	unittest.main()
