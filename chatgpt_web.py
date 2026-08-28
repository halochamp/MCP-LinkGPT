"""Deterministic ChatGPT Web control built on browser-use's CDP session."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable, Iterator
from urllib.parse import urlparse

# browser-use emits startup logs unless these are set before importing it. MCP
# reserves stdout for JSON-RPC, so any diagnostics must stay on stderr.
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "critical")
os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")

from browser_use.browser import BrowserSession


CHATGPT_URL = "https://chatgpt.com/"
ALLOWED_DOMAINS = ["chatgpt.com", "*.chatgpt.com", "auth.openai.com", "*.openai.com"]
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_DIR = Path("~/.codex-chatgpt/browser-profile").expanduser()
DEFAULT_WINDOW_WIDTH = 760
DEFAULT_WINDOW_HEIGHT = 560
DEFAULT_WINDOW_X = 24
DEFAULT_WINDOW_Y = 60
MAX_PROMPT_CHARS = 50_000
MAX_RESPONSE_CHARS = 100_000
MAX_PROGRESS_TAIL_CHARS = 1_200
CHATGPT_ORIGIN_GUARD_JS = """
const currentUrl = new URL(location.href);
const currentHost = currentUrl.hostname.toLowerCase().replace(/\\.$/, '');
if (
  currentUrl.protocol !== 'https:' ||
  (currentUrl.port !== '' && currentUrl.port !== '443') ||
  currentUrl.username !== '' ||
  currentUrl.password !== '' ||
  (currentHost !== 'chatgpt.com' && !currentHost.endsWith('.chatgpt.com'))
) return false;
"""


class ChatGPTWebError(RuntimeError):
	"""Expected bridge failure safe to return through MCP."""


class LoginRequiredError(ChatGPTWebError):
	"""The dedicated browser profile is not signed in to ChatGPT."""


class ChallengeDetectedError(ChatGPTWebError):
	"""A CAPTCHA or browser-verification challenge requires a human."""


class CookieSafeBrowserSession(BrowserSession):
	"""Use browser-use's lifecycle without its storage-state cookie watchdog.

	browser-use 0.12.9 has no public switch for this watchdog. Its
	``attach_all_watchdogs`` method enables it whenever ``user_data_dir`` is set,
	which is required here for a persistent login profile. Mask the persistence
	fields only while watchdogs are attached, then restore them before Chrome is
	launched so the browser still uses the requested profile.
	"""

	async def attach_all_watchdogs(self) -> None:
		profile = self.browser_profile
		original_user_data_dir = profile.user_data_dir
		original_storage_state = profile.storage_state
		object.__setattr__(profile, "user_data_dir", None)
		object.__setattr__(profile, "storage_state", None)
		try:
			await super().attach_all_watchdogs()
		finally:
			object.__setattr__(profile, "user_data_dir", original_user_data_dir)
			object.__setattr__(profile, "storage_state", original_storage_state)

		if getattr(self, "_storage_state_watchdog", None) is not None:
			raise RuntimeError("browser-use attached its storage-state watchdog; refusing to start")


SessionFactory = Callable[..., BrowserSession]
SleepFunction = Callable[[float], Awaitable[None]]
ProgressReporter = Callable[[float, str], Awaitable[None]]


def _canonical_profile_dir(value: Path | str) -> Path:
	try:
		profile_dir = Path(value).expanduser().resolve(strict=False)
	except (OSError, RuntimeError) as exc:
		raise ChatGPTWebError("The ChatGPT browser profile path could not be resolved.") from exc
	if profile_dir == REPOSITORY_ROOT or REPOSITORY_ROOT in profile_dir.parents:
		raise ChatGPTWebError("The ChatGPT browser profile must be outside this repository.")
	return profile_dir


class ChatGPTWebBridge:
	"""Own one headed browser session and serialize ChatGPT requests."""

	def __init__(
		self,
		*,
		profile_dir: Path | None = None,
		session_factory: SessionFactory = CookieSafeBrowserSession,
		sleep: SleepFunction = asyncio.sleep,
		poll_interval: float = 0.75,
	) -> None:
		configured_profile = os.environ.get("CODEX_CHATGPT_PROFILE_DIR")
		configured_value = configured_profile if configured_profile else (profile_dir or DEFAULT_PROFILE_DIR)
		self.profile_dir = _canonical_profile_dir(configured_value)
		self._session_factory = session_factory
		self._sleep = sleep
		self._poll_interval = poll_interval
		self._session: BrowserSession | None = None
		self._lock = asyncio.Lock()
		self._profile_lock_handle: BinaryIO | None = None
		self._ownership_marker_path: Path | None = None
		self._reconnect_blocked = False
		self._operation_target_id: str | None = None
		self._operation_page_identity: tuple[str, str | None] | None = None
		self._operation_response_identity: tuple[str, str | None] | None = None
		self._operation_user_count_before: int | None = None
		self._operation_allow_new_chat_transition = False
		self._operation_new_chat_transition_count = 0

	@contextmanager
	def _operation_scope(self) -> Iterator[None]:
		previous_target_id = self._operation_target_id
		previous_page_identity = self._operation_page_identity
		previous_response_identity = self._operation_response_identity
		previous_user_count_before = self._operation_user_count_before
		previous_allow_new_chat_transition = self._operation_allow_new_chat_transition
		previous_new_chat_transition_count = self._operation_new_chat_transition_count
		self._operation_target_id = None
		self._operation_page_identity = None
		self._operation_response_identity = None
		self._operation_user_count_before = None
		self._operation_allow_new_chat_transition = False
		self._operation_new_chat_transition_count = 0
		try:
			yield
		except asyncio.CancelledError:
			# Cancellation can interrupt a remote CDP operation after the browser has
			# applied its side effect but before the response reached this process.
			if self._session is not None:
				self._reconnect_blocked = True
			raise
		finally:
			self._operation_target_id = previous_target_id
			self._operation_page_identity = previous_page_identity
			self._operation_response_identity = previous_response_identity
			self._operation_user_count_before = previous_user_count_before
			self._operation_allow_new_chat_transition = previous_allow_new_chat_transition
			self._operation_new_chat_transition_count = previous_new_chat_transition_count

	def _acquire_profile_lock(self) -> None:
		if self._profile_lock_handle is not None:
			return
		self._prepare_profile_dir()
		lock_path = self._profile_lock_path()
		no_follow = getattr(os, "O_NOFOLLOW", 0)
		if not no_follow:
			raise ChatGPTWebError("This system cannot safely create the ChatGPT profile lock.")
		file_descriptor: int | None = None
		try:
			file_descriptor = os.open(
				lock_path,
				os.O_CREAT | os.O_RDWR | no_follow,
				0o600,
			)
			handle = os.fdopen(file_descriptor, "a+b")
		except OSError as exc:
			if file_descriptor is not None:
				with suppress(OSError):
					os.close(file_descriptor)
			raise ChatGPTWebError("The ChatGPT profile lock could not be opened safely.") from exc
		try:
			info = os.fstat(handle.fileno())
			if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
				raise ChatGPTWebError("The ChatGPT profile lock is not a safe regular file.")
			os.fchmod(handle.fileno(), 0o600)
			fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
		except BlockingIOError as exc:
			handle.close()
			raise ChatGPTWebError(
				"The dedicated ChatGPT browser profile is already in use by another MCP process."
			) from exc
		except ChatGPTWebError:
			handle.close()
			raise
		except OSError as exc:
			handle.close()
			raise ChatGPTWebError("The ChatGPT profile lock could not be secured safely.") from exc
		self._profile_lock_handle = handle

	def _release_profile_lock(self) -> None:
		handle = self._profile_lock_handle
		self._profile_lock_handle = None
		if handle is None:
			return
		with suppress(OSError):
			fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
		handle.close()

	def _profile_lock_path(self) -> Path:
		return self.profile_dir.parent / f".{self.profile_dir.name}.lock"

	def _ownership_marker_file(self) -> Path:
		return self.profile_dir.parent / f".{self.profile_dir.name}.unclean"

	@staticmethod
	def _require_owned_directory(path: Path, label: str, *, normalize_mode: bool = False) -> None:
		try:
			info = path.lstat()
		except OSError as exc:
			raise ChatGPTWebError(f"The ChatGPT {label} could not be inspected safely.") from exc
		if not stat.S_ISDIR(info.st_mode):
			raise ChatGPTWebError(f"The ChatGPT {label} must be a directory.")
		if info.st_uid != os.getuid():
			raise ChatGPTWebError(f"The ChatGPT {label} must be owned by the current user.")
		if info.st_mode & 0o022:
			if not normalize_mode:
				raise ChatGPTWebError(f"The ChatGPT {label} must not be writable by another user.")
			try:
				os.chmod(path, 0o700)
				info = path.lstat()
			except OSError as exc:
				raise ChatGPTWebError(f"The ChatGPT {label} permissions could not be secured.") from exc
			if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
				raise ChatGPTWebError(f"The ChatGPT {label} permissions could not be secured.")

	def _prepare_profile_dir(self) -> None:
		canonical_before = _canonical_profile_dir(self.profile_dir)
		if canonical_before != self.profile_dir:
			raise ChatGPTWebError("The ChatGPT browser profile path changed before use.")
		parent = self.profile_dir.parent
		try:
			parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		except OSError as exc:
			raise ChatGPTWebError("The ChatGPT browser profile directory could not be created.") from exc
		self._require_owned_directory(parent, "profile parent")

		info: os.stat_result | None = None
		try:
			info = self.profile_dir.lstat()
		except FileNotFoundError:
			try:
				self.profile_dir.mkdir(mode=0o700)
			except FileExistsError:
				info = self.profile_dir.lstat()
			except OSError as exc:
				raise ChatGPTWebError("The ChatGPT browser profile directory could not be created.") from exc
		else:
			if stat.S_ISLNK(info.st_mode):
				raise ChatGPTWebError("The ChatGPT browser profile must not be a symlink.")

		if info is None:
			info = self.profile_dir.lstat()
		if not stat.S_ISDIR(info.st_mode):
			raise ChatGPTWebError("The ChatGPT browser profile must be a directory.")
		if info.st_uid != os.getuid():
			raise ChatGPTWebError("The ChatGPT browser profile must be owned by the current user.")
		try:
			os.chmod(self.profile_dir, 0o700)
			info = self.profile_dir.lstat()
		except OSError as exc:
			raise ChatGPTWebError("The ChatGPT browser profile permissions could not be secured.") from exc
		if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
			raise ChatGPTWebError("The ChatGPT browser profile permissions could not be secured.")

		canonical_after = _canonical_profile_dir(self.profile_dir)
		if canonical_after != self.profile_dir:
			raise ChatGPTWebError("The ChatGPT browser profile path changed before use.")

	def _raise_stale_ownership_marker(self) -> None:
		marker = self._ownership_marker_file()
		raise ChatGPTWebError(
			"The ChatGPT browser profile has uncertain ownership from an earlier process. "
			f"Confirm the dedicated browser is stopped, remove {marker}, then restart the MCP server."
		)

	def _assert_no_stale_ownership_marker(self) -> None:
		marker = self._ownership_marker_file()
		try:
			marker.lstat()
		except FileNotFoundError:
			return
		except OSError as exc:
			raise ChatGPTWebError("The ChatGPT browser ownership marker could not be inspected safely.") from exc
		self._raise_stale_ownership_marker()

	def _create_ownership_marker(self) -> None:
		marker = self._ownership_marker_file()
		no_follow = getattr(os, "O_NOFOLLOW", 0)
		if not no_follow:
			raise ChatGPTWebError("This system cannot safely create the ChatGPT ownership marker.")
		try:
			file_descriptor = os.open(
				marker,
				os.O_CREAT | os.O_EXCL | os.O_WRONLY | no_follow,
				0o600,
			)
		except FileExistsError as exc:
			self._raise_stale_ownership_marker()
			raise AssertionError("unreachable") from exc
		except OSError as exc:
			raise ChatGPTWebError("The ChatGPT browser ownership marker could not be created safely.") from exc
		else:
			os.close(file_descriptor)
			self._ownership_marker_path = marker

	def _clear_ownership_marker(self) -> None:
		marker = self._ownership_marker_path
		if marker is None:
			return
		try:
			info = marker.lstat()
			if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
				raise ChatGPTWebError("The ChatGPT browser ownership marker is not safe to remove.")
			marker.unlink()
		except FileNotFoundError:
			pass
		except ChatGPTWebError:
			raise
		except OSError as exc:
			raise ChatGPTWebError("The ChatGPT browser ownership marker could not be cleared.") from exc
		self._ownership_marker_path = None

	def _finalize_confirmed_shutdown(self, session: BrowserSession | None) -> None:
		self._clear_ownership_marker()
		if session is None or self._session is session:
			self._session = None
			self._reconnect_blocked = False
			self._release_profile_lock()

	@staticmethod
	def _window_setting(name: str, default: int, *, minimum: int, maximum: int) -> int:
		raw_value = os.environ.get(name)
		if raw_value is None:
			return default
		try:
			value = int(raw_value)
		except ValueError as exc:
			raise ChatGPTWebError(f"{name} must be an integer.") from exc
		if not minimum <= value <= maximum:
			raise ChatGPTWebError(f"{name} must be between {minimum} and {maximum}.")
		return value

	@classmethod
	def _window_options(cls) -> tuple[dict[str, int], dict[str, int]]:
		return (
			{
				"width": cls._window_setting(
					"CODEX_CHATGPT_WINDOW_WIDTH", DEFAULT_WINDOW_WIDTH, minimum=400, maximum=4000
				),
				"height": cls._window_setting(
					"CODEX_CHATGPT_WINDOW_HEIGHT", DEFAULT_WINDOW_HEIGHT, minimum=300, maximum=4000
				),
			},
			{
				"width": cls._window_setting("CODEX_CHATGPT_WINDOW_X", DEFAULT_WINDOW_X, minimum=0, maximum=10000),
				"height": cls._window_setting("CODEX_CHATGPT_WINDOW_Y", DEFAULT_WINDOW_Y, minimum=0, maximum=10000),
			},
		)

	async def _ensure_session(self) -> BrowserSession:
		if self._reconnect_blocked:
			raise ChatGPTWebError(
				"The previous ChatGPT browser session could not be shut down safely. Restart the MCP server before retrying."
			)
		if self._session is not None:
			return self._session

		window_size, window_position = self._window_options()
		self._acquire_profile_lock()
		try:
			self._assert_no_stale_ownership_marker()
			self._create_ownership_marker()
		except Exception:
			self._release_profile_lock()
			raise

		kwargs: dict[str, Any] = {
			"headless": False,
			"user_data_dir": self.profile_dir,
			"storage_state": None,
			"enable_default_extensions": False,
			"window_size": window_size,
			"window_position": window_position,
			"allowed_domains": ALLOWED_DOMAINS,
			"keep_alive": False,
			"minimum_wait_page_load_time": 0.5,
			"wait_for_network_idle_page_load_time": 1.0,
		}
		# Let browser-use discover the local executable. In browser-use 0.12.9,
		# explicitly naming Google Chrome makes BrowserProfile copy user_data_dir
		# to a disposable temp directory, so login state would not persist.

		partial_session: BrowserSession | None = None
		try:
			partial_session = self._session_factory(**kwargs)
			self._session = partial_session
			self._reconnect_blocked = True
			await partial_session.start()
		except asyncio.CancelledError:
			# Cancellation can leave a browser process behind. Retain ownership and
			# force an explicit close/restart instead of releasing the profile lock.
			self._reconnect_blocked = True
			if partial_session is not None:
				self._session = partial_session
			raise
		except Exception as exc:
			if partial_session is None:
				# The factory failed before it created a session, so there is no
				# browser ownership left to clean up.
				self._finalize_confirmed_shutdown(None)
			else:
				try:
					await partial_session.kill()
				except asyncio.CancelledError:
					self._reconnect_blocked = True
					self._session = partial_session
					raise
				except Exception:
					self._reconnect_blocked = True
					self._session = partial_session
				else:
					self._finalize_confirmed_shutdown(partial_session)
			raise ChatGPTWebError(
				f"Could not start the dedicated browser session ({type(exc).__name__})."
			) from exc
		self._reconnect_blocked = False
		return partial_session

	async def _invalidate_session(self, session: BrowserSession) -> None:
		if self._session is not session:
			return
		self._reconnect_blocked = True
		try:
			await session.kill()
		except asyncio.CancelledError:
			# The termination outcome is unknown. Keep both the session reference
			# and the profile lock so a later close can retry safely.
			self._reconnect_blocked = True
			raise
		except Exception:
			return
		self._finalize_confirmed_shutdown(session)

	async def _get_bound_cdp_session(self, session: BrowserSession) -> Any:
		requested_target_id = self._operation_target_id
		cdp_session = await session.get_or_create_cdp_session(target_id=requested_target_id, focus=False)
		if not cdp_session:
			return None
		target_id = getattr(cdp_session, "target_id", None)
		if not isinstance(target_id, str) or not target_id:
			raise ChatGPTWebError("The browser did not provide a stable ChatGPT target.")
		if requested_target_id is None:
			self._operation_target_id = target_id
		elif target_id != requested_target_id:
			raise ChatGPTWebError("The ChatGPT browser target changed during the operation.")
		return cdp_session

	async def _evaluate(self, expression: str) -> Any:
		session = await self._ensure_session()
		try:
			cdp_session = await self._get_bound_cdp_session(session)
			if not cdp_session:
				await self._invalidate_session(session)
				raise ChatGPTWebError("No active ChatGPT browser tab is available.")
			result = await cdp_session.cdp_client.send.Runtime.evaluate(
				params={"expression": expression, "returnByValue": True, "awaitPromise": True},
				session_id=cdp_session.session_id,
			)
		except asyncio.CancelledError:
			self._reconnect_blocked = True
			raise
		except ChatGPTWebError:
			raise
		except Exception as exc:
			await self._invalidate_session(session)
			raise ChatGPTWebError("The browser connection was lost while inspecting ChatGPT.") from exc
		if "exceptionDetails" in result:
			raise ChatGPTWebError("ChatGPT page script failed to execute.")
		return result.get("result", {}).get("value")

	async def _insert_text(self, text: str) -> None:
		await self._assert_interactive_page(expected_identity=self._operation_page_identity)
		inserted = await self._evaluate(
			f"""
			(() => {{
			  {self._mutation_guard_js(self._operation_page_identity)}
			  const value = {json.dumps(text)};
			  const el = document.querySelector(
			    '#prompt-textarea, textarea[data-testid="prompt-textarea"], div[contenteditable="true"][data-virtualkeyboard="true"]'
			  );
			  const visible = (node) => !!node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
			  if (!visible(el)) return false;
			  el.focus();
			  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {{
			    const prototype = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
			    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
			    if (!setter) return false;
			    setter.call(el, value);
			    el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: value}}));
			    return true;
			  }}
			  const selection = window.getSelection();
			  if (!selection) return false;
			  const range = document.createRange();
			  range.selectNodeContents(el);
			  selection.removeAllRanges();
			  selection.addRange(range);
			  return document.execCommand('insertText', false, value);
			}})()
			"""
		)
		if not inserted:
			raise ChatGPTWebError("Could not insert text into the ChatGPT prompt composer.")

	async def _navigate(self, url: str = CHATGPT_URL) -> None:
		if not self._is_interactive_chatgpt_url(url):
			raise ChatGPTWebError("Navigation outside chatgpt.com is not allowed.")
		session = await self._ensure_session()
		try:
			cdp_session = await self._get_bound_cdp_session(session)
			if not cdp_session:
				await self._invalidate_session(session)
				raise ChatGPTWebError("No active ChatGPT browser tab is available.")
			result = await cdp_session.cdp_client.send.Page.navigate(
				params={"url": url},
				session_id=cdp_session.session_id,
			)
			if not isinstance(result, dict) or result.get("errorText"):
				raise RuntimeError("Page.navigate failed.")
		except asyncio.CancelledError:
			self._reconnect_blocked = True
			raise
		except Exception as exc:
			await self._invalidate_session(session)
			raise ChatGPTWebError("The browser could not navigate to ChatGPT.") from exc
		await self._wait_for_approved_navigation()

	async def _wait_for_approved_navigation(self, timeout_seconds: float = 15.0) -> None:
		deadline = time.monotonic() + timeout_seconds
		while True:
			state = await self._page_state()
			url = state.get("url")
			if self._is_interactive_chatgpt_url(url) or self._is_openai_auth_url(url):
				return
			if time.monotonic() >= deadline:
				raise ChatGPTWebError("The browser did not navigate to an approved ChatGPT page.")
			await self._sleep(max(self._poll_interval, 0.05))

	@staticmethod
	def _is_approved_url(url: object, hostname_root: str) -> bool:
		if not isinstance(url, str):
			return False
		try:
			parsed = urlparse(url)
			port = parsed.port
		except ValueError:
			return False
		hostname = (parsed.hostname or "").lower().rstrip(".")
		return (
			parsed.scheme.lower() == "https"
			and parsed.username is None
			and parsed.password is None
			and port in (None, 443)
			and (hostname == hostname_root or hostname.endswith(f".{hostname_root}"))
		)

	@classmethod
	def _is_interactive_chatgpt_url(cls, url: object) -> bool:
		return cls._is_approved_url(url, "chatgpt.com")

	@classmethod
	def _is_openai_auth_url(cls, url: object) -> bool:
		return cls._is_approved_url(url, "auth.openai.com")

	@classmethod
	def _is_conversation_url(cls, url: object) -> bool:
		if not cls._is_interactive_chatgpt_url(url):
			return False
		parsed = urlparse(url)
		return parsed.path == "/c" or parsed.path.startswith("/c/")

	@classmethod
	def _is_provisional_conversation_url(cls, url: object) -> bool:
		if not cls._is_conversation_url(url):
			return False
		return urlparse(url).path.startswith("/c/WEB:")

	@staticmethod
	def _page_identity(state: dict[str, Any]) -> tuple[str, str | None] | None:
		url = state.get("url")
		if not isinstance(url, str):
			return None
		document_token = state.get("document_token")
		if not isinstance(document_token, str) or not document_token:
			return None
		return url, document_token

	@staticmethod
	def _mutation_guard_js(expected_identity: tuple[str, str | None] | None = None) -> str:
		guard = CHATGPT_ORIGIN_GUARD_JS
		if expected_identity is not None:
			expected_url, expected_document_token = expected_identity
			guard += f"if (location.href !== {json.dumps(expected_url)}) return false;\n"
			if expected_document_token is not None:
				guard += (
					"if (String(performance.timeOrigin || '') !== "
					f"{json.dumps(expected_document_token)}) return false;\n"
				)
		return guard

	@classmethod
	def _raise_for_page_state(cls, state: dict[str, Any]) -> None:
		if state.get("challenge"):
			raise ChallengeDetectedError(
				"ChatGPT displayed a human-verification challenge. Complete it manually in the opened browser, then retry."
			)
		url = state.get("url")
		if cls._is_openai_auth_url(url):
			raise LoginRequiredError(
				"ChatGPT login is required. Sign in manually in the opened dedicated Chrome window, then retry."
			)
		if state.get("login_required") and (
			cls._is_interactive_chatgpt_url(url)
		):
			raise LoginRequiredError(
				"ChatGPT login is required. Sign in manually in the opened dedicated Chrome window, then retry."
			)
		if not cls._is_interactive_chatgpt_url(url):
			raise ChatGPTWebError("The active browser page is outside the approved ChatGPT domain.")

	async def _assert_interactive_page(
		self, *, expected_identity: tuple[str, str | None] | None = None
	) -> dict[str, Any]:
		state = await self._page_state()
		self._raise_for_page_state(state)
		if expected_identity is not None and self._page_identity(state) != expected_identity:
			raise ChatGPTWebError("The ChatGPT page changed during the operation.")
		return state

	@staticmethod
	def _normalized_text(value: object) -> str:
		return " ".join(str(value or "").split())

	@staticmethod
	def _progress_response_tail(response: str) -> str:
		"""Bound progress content while preserving enough recent context to review."""
		tail = response.strip()
		if len(tail) <= MAX_PROGRESS_TAIL_CHARS:
			return tail
		return "…" + tail[-MAX_PROGRESS_TAIL_CHARS:]

	def _submitted_user_turn_is_visible(self, state: dict[str, Any], prompt: str) -> bool:
		before_count = self._operation_user_count_before
		if before_count is None:
			raise ChatGPTWebError("The ChatGPT request has no bound user-turn baseline.")
		try:
			current_count = int(state.get("user_count", -1))
		except (TypeError, ValueError) as exc:
			raise ChatGPTWebError("The ChatGPT page returned an invalid user-turn count.") from exc
		if current_count < 0:
			raise ChatGPTWebError("The ChatGPT page returned an invalid user-turn count.")

		expected_count = before_count + 1
		if current_count < expected_count:
			return False
		if current_count != expected_count:
			raise ChatGPTWebError("The ChatGPT user turn changed during response collection.")

		latest_user_text = self._normalized_text(state.get("latest_user_text"))
		if not latest_user_text:
			return False
		if latest_user_text != self._normalized_text(prompt):
			raise ChatGPTWebError("The ChatGPT user turn changed during response collection.")
		return True

	async def _response_page_state(self, prompt: str) -> dict[str, Any] | None:
		state = await self._page_state()
		self._raise_for_page_state(state)
		current_identity = self._page_identity(state)
		bound_identity = self._operation_response_identity
		if current_identity is None or bound_identity is None:
			raise ChatGPTWebError("The ChatGPT response page has no stable identity.")
		if current_identity == bound_identity:
			return state if self._submitted_user_turn_is_visible(state, prompt) else None
		if self._operation_allow_new_chat_transition and self._is_conversation_url(current_identity[0]):
			if not self._submitted_user_turn_is_visible(state, prompt):
				# The route can commit before the newly submitted user turn is
				# rendered. Keep polling, but never treat an unrelated assistant
				# message on that route as this request's response.
				return None
			if self._operation_new_chat_transition_count >= 2:
				raise ChatGPTWebError("The ChatGPT conversation changed during response collection.")
			is_provisional = self._is_provisional_conversation_url(current_identity[0])
			if self._operation_new_chat_transition_count == 1 and is_provisional:
				raise ChatGPTWebError("The ChatGPT conversation changed during response collection.")
			self._operation_new_chat_transition_count += 1
			self._operation_response_identity = current_identity
			self._operation_allow_new_chat_transition = (
				self._operation_new_chat_transition_count == 1 and is_provisional
			)
			return state
		raise ChatGPTWebError("The ChatGPT conversation changed during response collection.")

	async def _page_state(self) -> dict[str, Any]:
		state = await self._evaluate(
			"""
			(() => {
			  const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
			  const composer = document.querySelector(
			    '#prompt-textarea, textarea[data-testid="prompt-textarea"], div[contenteditable="true"][data-virtualkeyboard="true"]'
			  );
			  const assistant = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
			  const latest = assistant.length ? (assistant[assistant.length - 1].innerText || '').trim() : '';
			  const user = Array.from(document.querySelectorAll('[data-message-author-role="user"]'));
			  const latestUser = user.length ? (user[user.length - 1].innerText || '').trim() : '';
			  const stop = Array.from(document.querySelectorAll('button[data-testid="stop-button"], button[aria-label]')).some((button) => {
			    const label = (button.getAttribute('aria-label') || '').trim().toLowerCase();
			    const testId = (button.getAttribute('data-testid') || '').trim().toLowerCase();
			    return visible(button) && (
			      testId === 'stop-button' || label.includes('stop generating') ||
			      label.includes('stop response') || label === 'หยุดสร้างคำตอบ'
			    );
			  });
			  const challengeNode = document.querySelector(
			    'iframe[src*="challenge"], iframe[src*="captcha"], #challenge-stage, [id^="cf-chl"], form[action*="challenge"]'
			  );
			  const challengeTitle = ['just a moment...', 'attention required!'].includes(document.title.trim().toLowerCase());
			  const challenge = visible(challengeNode) || (!visible(composer) && challengeTitle);
			  const currentUrl = new URL(location.href);
			  const currentHost = currentUrl.hostname.toLowerCase().replace(/\\.$/, '');
			  const isAuthHost = currentUrl.protocol === 'https:' && currentUrl.port === '' &&
				currentUrl.username === '' && currentUrl.password === '' &&
				(currentHost === 'auth.openai.com' || currentHost.endsWith('.auth.openai.com'));
			  const isConversationContent = (el) => !!el.closest(
				'[data-message-author-role], [data-testid^="conversation-turn"], [data-testid*="conversation"]'
			  );
			  const inSiteChrome = (el) => !!el.closest('header, nav, [role="banner"]');
			  const authRoute = (el) => {
				const href = el.getAttribute('href');
				if (!href) return false;
				try {
				  const destination = new URL(href, location.href);
				  const destinationHost = destination.hostname.toLowerCase().replace(/\\.$/, '');
				  const approvedHost = destinationHost === 'chatgpt.com' || destinationHost.endsWith('.chatgpt.com') ||
					 destinationHost === 'auth.openai.com' || destinationHost.endsWith('.auth.openai.com');
				  const authPath = /(^|\\/)(auth\\/)?(login|signup)(\\/|$)/.test(destination.pathname.toLowerCase());
				  return destination.protocol === 'https:' && approvedHost && authPath;
				} catch (_) {
				  return false;
				}
			  };
			  const loginControls = Array.from(document.querySelectorAll(
				'header a[href], header button, nav a[href], nav button, [role="banner"] a[href], [role="banner"] button, a[href], button[data-testid]'
			  )).filter((el) => visible(el) && !isConversationContent(el));
			  const loginLabels = ['log in', 'login', 'sign up', 'เข้าสู่ระบบ', 'สมัคร'];
			  const loginLink = isAuthHost || loginControls.some((el) => {
				const testId = (el.getAttribute('data-testid') || '').trim().toLowerCase();
				const text = (el.innerText || '').trim().toLowerCase();
				const stableTestId = /(^|[-_])(log[-_]?in|sign[-_]?up|login|signup)([-_]|$)/.test(testId);
				return authRoute(el) || stableTestId || (inSiteChrome(el) && loginLabels.includes(text));
			  });
			  return {
				url: location.href,
				title: document.title,
				document_token: String(performance.timeOrigin || ''),
				composer_ready: visible(composer),
				assistant_count: assistant.length,
				latest_text: latest,
				user_count: user.length,
				latest_user_text: latestUser,
				streaming: stop,
			    challenge,
			    login_required: loginLink
			  };
			})()
			"""
		)
		if not isinstance(state, dict):
			raise ChatGPTWebError("Could not inspect the ChatGPT page.")
		return state

	async def _wait_for_composer(self, timeout_seconds: float = 15.0) -> dict[str, Any]:
		deadline = time.monotonic() + timeout_seconds
		last_state: dict[str, Any] = {}
		while time.monotonic() < deadline:
			last_state = await self._page_state()
			self._raise_for_page_state(last_state)
			if last_state.get("composer_ready"):
				return last_state
			await self._sleep(self._poll_interval)
		raise ChatGPTWebError(
			f"ChatGPT did not become ready within {timeout_seconds:g} seconds (page: {last_state.get('title', 'unknown')})."
		)

	async def _focus_and_clear_composer(self) -> None:
		expected_identity = self._operation_page_identity
		await self._assert_interactive_page(expected_identity=expected_identity)
		focused = await self._evaluate(
			f"""
			(() => {{
			  {self._mutation_guard_js(expected_identity)}
			  const el = document.querySelector(
			    '#prompt-textarea, textarea[data-testid="prompt-textarea"], div[contenteditable="true"][data-virtualkeyboard="true"]'
			  );
			  if (!el) return false;
			  el.focus();
			  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {{
			    el.select();
			    el.value = '';
			    el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'deleteContentBackward'}}));
			  }} else {{
			    const selection = window.getSelection();
			    const range = document.createRange();
			    range.selectNodeContents(el);
			    selection.removeAllRanges();
			    selection.addRange(range);
			    document.execCommand('delete');
			  }}
			  return true;
			}})()
			"""
		)
		if not focused:
			raise ChatGPTWebError("Could not focus the ChatGPT prompt composer.")

	async def _submit(self, prompt: str, timeout_seconds: float = 8.0) -> None:
		expected_identity = self._operation_page_identity
		deadline = time.monotonic() + timeout_seconds
		while time.monotonic() < deadline:
			await self._assert_interactive_page(expected_identity=expected_identity)
			clicked = await self._evaluate(
				f"""
				(() => {{
				  {self._mutation_guard_js(expected_identity)}
				  const expectedPrompt = {json.dumps(prompt)};
				  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
				  const composer = document.querySelector(
				    '#prompt-textarea, textarea[data-testid="prompt-textarea"], div[contenteditable="true"][data-virtualkeyboard="true"]'
				  );
				  const visible = (node) => !!node && !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
				  if (!visible(composer)) return false;
				  const composerValue = composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement
				    ? composer.value : (composer.innerText || composer.textContent || '');
				  if (normalize(composerValue) !== normalize(expectedPrompt)) return false;
				  const selectors = [
				    'button[data-testid="send-button"]',
				    'button[aria-label="Send prompt"]',
				    'button[aria-label="ส่งข้อความ"]'
				  ];
				  const button = selectors.map((selector) => document.querySelector(selector)).find(Boolean);
				  if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
				  button.click();
				  return true;
				}})()
				"""
			)
			if clicked:
				return
			await self._sleep(0.2)
		raise ChatGPTWebError("The ChatGPT send button did not become available.")

	async def _wait_until_idle(self, timeout_seconds: float = 60.0) -> dict[str, Any]:
		deadline = time.monotonic() + timeout_seconds
		last_state: dict[str, Any] = {}
		while time.monotonic() < deadline:
			last_state = await self._page_state()
			self._raise_for_page_state(last_state)
			if not last_state.get("streaming"):
				return last_state
			await self._sleep(self._poll_interval)
		raise ChatGPTWebError(
			"A previous ChatGPT response is still running. Wait for it to finish, then retry or read it with chatgpt_last_response."
		)

	async def status(self) -> dict[str, Any]:
		async with self._lock:
			with self._operation_scope():
				await self._ensure_session()
				state = await self._page_state()
				url = state.get("url")
				if not self._is_interactive_chatgpt_url(url) and not self._is_openai_auth_url(url):
					await self._navigate()
					state = await self._page_state()
				url = state.get("url")
				if not self._is_interactive_chatgpt_url(url) and not self._is_openai_auth_url(url):
					raise ChatGPTWebError("The active browser page is outside the approved ChatGPT domain.")
				status = (
					"challenge"
					if state.get("challenge")
					else "login_required"
					if self._is_openai_auth_url(url) or state.get("login_required")
					else "ready"
					if state.get("composer_ready")
					else "loading"
				)
				return {
					"ok": status == "ready" and self._is_interactive_chatgpt_url(url),
					"status": status,
					"url": state.get("url"),
					"assistant_messages": state.get("assistant_count", 0),
					"profile_dir": str(self.profile_dir),
				}

	async def new_chat(self) -> dict[str, Any]:
		async with self._lock:
			with self._operation_scope():
				await self._navigate()
				state = await self._wait_for_composer()
				return {"ok": True, "status": "ready", "url": state.get("url")}

	async def last_response(self) -> dict[str, Any]:
		async with self._lock:
			with self._operation_scope():
				await self._ensure_session()
				state = await self._page_state()
				self._raise_for_page_state(state)
				response = str(state.get("latest_text", "")).strip()
				if not response:
					raise ChatGPTWebError("No ChatGPT assistant response is available in the current conversation.")
				truncated = len(response) > MAX_RESPONSE_CHARS
				streaming = bool(state.get("streaming"))
				return {
					"ok": True,
					"status": "in_progress" if streaming else "completed",
					"message": (
						"ChatGPT is still generating a response."
						if streaming
						else "ChatGPT has finished and the final response is ready."
					),
					"response": response[:MAX_RESPONSE_CHARS],
					"truncated": truncated,
					"streaming": streaming,
					"url": state.get("url"),
				}

	async def ask(
		self,
		prompt: str,
		*,
		new_chat: bool = True,
		timeout_seconds: int = 600,
		progress: ProgressReporter | None = None,
	) -> dict[str, Any]:
		if not isinstance(prompt, str) or not prompt.strip():
			raise ChatGPTWebError("Prompt must be a non-empty string.")
		if len(prompt) > MAX_PROMPT_CHARS:
			raise ChatGPTWebError(f"Prompt exceeds the {MAX_PROMPT_CHARS:,}-character limit.")
		if timeout_seconds < 10 or timeout_seconds > 900:
			raise ChatGPTWebError("timeout_seconds must be between 10 and 900.")

		async def report(percent: float, message: str) -> None:
			if progress is not None:
				await progress(percent, message)

		async with self._lock:
			with self._operation_scope():
				started = time.monotonic()
				await report(5, "Opening the dedicated ChatGPT session.")
				await self._ensure_session()
				if new_chat:
					await report(15, "Opening a fresh ChatGPT conversation.")
					await self._navigate()
				await report(25, "Preparing the ChatGPT prompt.")
				before = await self._wait_for_composer()
				if before.get("streaming"):
					before = await self._wait_until_idle(timeout_seconds=min(60, timeout_seconds))
				before_count = int(before.get("assistant_count", 0))
				try:
					before_user_count = int(before.get("user_count", -1))
				except (TypeError, ValueError) as exc:
					raise ChatGPTWebError("The ChatGPT page returned an invalid user-turn count.") from exc
				if before_user_count < 0:
					raise ChatGPTWebError("The ChatGPT page returned an invalid user-turn count.")
				before_identity = self._page_identity(before)
				if before_identity is None:
					raise ChatGPTWebError("The ChatGPT page has no stable identity for this operation.")
				self._operation_page_identity = before_identity

				await self._focus_and_clear_composer()
				await self._insert_text(prompt)
				await self._submit(prompt)
				await report(35, "Question sent. Waiting for ChatGPT to start responding.")
				self._operation_response_identity = before_identity
				self._operation_user_count_before = before_user_count
				self._operation_allow_new_chat_transition = new_chat and not self._is_conversation_url(
					before_identity[0]
				)

				deadline = time.monotonic() + timeout_seconds
				stable_text = ""
				stable_polls = 0
				latest_state: dict[str, Any] = {}
				last_wait_state: str | None = None
				last_progress_at = started
				last_reported_tail = ""
				while time.monotonic() < deadline:
					latest_state = await self._response_page_state(prompt)
					if latest_state is None:
						now = time.monotonic()
						if last_wait_state != "waiting_for_response" or now - last_progress_at >= 15:
							await report(45, "Question sent. Waiting for ChatGPT to start responding.")
							last_wait_state = "waiting_for_response"
							last_progress_at = now
						await self._sleep(self._poll_interval)
						continue
					latest_text = str(latest_state.get("latest_text", "")).strip()
					is_new = int(latest_state.get("assistant_count", 0)) > before_count
					is_streaming = bool(latest_state.get("streaming"))
					wait_state = (
						"generating"
						if is_new and is_streaming
						else "finalizing"
						if is_new
						else "waiting_for_response"
					)
					now = time.monotonic()
					response_tail = self._progress_response_tail(latest_text) if is_new and is_streaming else ""
					should_report_tail = (
						bool(response_tail)
						and response_tail != last_reported_tail
						and now - last_progress_at >= 3
					)
					if wait_state != last_wait_state or should_report_tail or now - last_progress_at >= 15:
						message = {
							"waiting_for_response": "Question sent. Waiting for ChatGPT to start responding.",
							"generating": "ChatGPT is generating its response.",
							"finalizing": "ChatGPT stopped generating; confirming the final response is stable.",
						}[wait_state]
						if response_tail:
							message = f"{message}\n\nLatest visible response tail:\n{response_tail}"
						await report(
							{"waiting_for_response": 45, "generating": 65, "finalizing": 85}[wait_state],
							message,
						)
						last_wait_state = wait_state
						last_progress_at = now
						if response_tail:
							last_reported_tail = response_tail
					if is_new and latest_text and not is_streaming:
						if latest_text == stable_text:
							stable_polls += 1
						else:
							stable_text = latest_text
							stable_polls = 1
						if stable_polls >= 3:
							truncated = len(latest_text) > MAX_RESPONSE_CHARS
							await report(100, "ChatGPT has finished and the final response is ready.")
							return {
								"ok": True,
								"status": "completed",
								"message": "ChatGPT has finished and the final response is ready.",
								"response": latest_text[:MAX_RESPONSE_CHARS],
								"truncated": truncated,
								"url": latest_state.get("url"),
								"elapsed_seconds": round(time.monotonic() - started, 2),
							}
					else:
						stable_polls = 0
					await self._sleep(self._poll_interval)

				raise ChatGPTWebError(
					f"Timed out after {timeout_seconds} seconds while waiting for ChatGPT to finish its response. "
					"The response may complete later; retrieve it with chatgpt_last_response."
				)

	async def close(self) -> None:
		async with self._lock:
			session = self._session
			if session is None:
				if not self._reconnect_blocked:
					self._release_profile_lock()
				return
			self._reconnect_blocked = True
			try:
				await session.kill()
			except asyncio.CancelledError:
				self._reconnect_blocked = True
				raise
			except Exception as exc:
				self._reconnect_blocked = True
				raise ChatGPTWebError("The dedicated browser did not shut down cleanly.") from exc
			if self._session is session:
				self._finalize_confirmed_shutdown(session)
