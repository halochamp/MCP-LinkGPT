# MCP-LinkGPT

`MCP-LinkGPT` is a small local MCP server that lets Codex explicitly consult
ChatGPT Web through a dedicated Chrome profile. Its intended role is a
**read-only reviewer and senior advisor**: it supplies a second opinion on code,
tests, designs, and proposed fixes. Codex remains responsible for gathering
evidence, deciding what to change, implementing changes, and verifying them.

```text
Codex -> MCP-LinkGPT server -> browser-use/CDP -> ChatGPT Web
```

It does not use an OpenAI API key, does not run a second browser LLM, and does
not modify `browser-use`. Browser operations are deterministic and limited to
the ChatGPT/OpenAI domains needed for the site and login.

The project name and MCP registration are `MCP-LinkGPT` / `mcp-linkgpt`. The
ChatGPT-facing Python identifiers, `chatgpt_*` tool names, and
`CODEX_CHATGPT_*` environment variables remain unchanged for compatibility;
they identify the connected service rather than the project display name.

## Tools

- `chatgpt_status()` opens the dedicated browser and reports `ready`,
  `login_required`, `challenge`, or `loading`.
- `chatgpt_close()` closes the dedicated browser session without sending or
  reading conversation content. The MCP registration remains available, and
  the next browser tool call can start a new session.
- `chatgpt_new_chat()` opens a fresh ChatGPT conversation.
- `chatgpt_last_response()` reads the latest completed or in-progress assistant
  response without sending another prompt.
- `chatgpt_ask(prompt, new_chat=true, timeout_seconds=600)` sends one prompt,
  waits for the response to stop changing, and returns the response text.

Only one process can own the dedicated profile at a time. Prompts and responses
are not written to application logs. CAPTCHA and human-verification pages are
never bypassed. The bridge canonicalizes the profile path and refuses a profile
inside this repository, including normal symlink aliases. It rechecks the
canonical path immediately before use, requires a user-owned non-world-writable
profile parent, and opens its lock without following symlinks.

The bridge disables browser-use's default extensions and its storage-state
watchdog. This is required because browser-use 0.12.9 otherwise polls cookie
values when a persistent `user_data_dir` is configured. The bridge never calls
cookie APIs or exports browser storage; the profile is used only by Chrome for
the signed-in browser session.

Each browser operation binds its CDP work to one target and checks the approved
ChatGPT origin at the point of every DOM mutation. If a user changes tabs or the
page navigates unexpectedly, the operation fails closed instead of following the
new target. If startup or shutdown is cancelled or cannot confirm that the
browser stopped, the bridge retains session ownership and the profile lock,
writes a persistent `.unclean` ownership marker, and blocks reconnect. Restart
alone is not recovery: first confirm that the dedicated browser is stopped,
remove the marker named in the error, then restart the MCP process. A cancellation
after a prompt has been submitted is not a rollback, so the next request must
wait for explicit cleanup.

Before sending, `chatgpt_ask` verifies the visible composer still contains the
exact normalized prompt in the same guarded runtime evaluation that clicks Send.
After sending, it requires exactly one new user turn whose latest text matches
that prompt before accepting a new assistant response. A manual or otherwise
ambiguous turn fails closed.

This MCP is directional: Codex can call ChatGPT Web, but ChatGPT Web cannot call
back through this server to read arbitrary local paths. ChatGPT Web may still
have a separate user-installed local connector, such as
`ENDEAVOR_AGENT_CHATGPT`; that is an independent tool route with its own access
policy. Reviews that use such a connector can take several minutes, so keep the
default 600-second timeout or raise it to at most 900 seconds when necessary.

## Review and advisory workflow

Use this tool for an independent critique, not for autonomous implementation.
The normal hand-off is:

1. Codex reads the relevant files, diff, test output, and project rules.
2. Codex sends ChatGPT a focused prompt with the necessary context.
3. ChatGPT returns findings, risks, alternatives, or a recommended decision.
4. Codex validates every material claim against the local codebase.
5. Codex implements only the changes that are in scope, then runs the relevant
   tests and reports the result.

Start a fresh chat for an independent review (`new_chat=true`). Use
`new_chat=false` only to ask a narrow follow-up about the same evidence. If the
response is still running after the timeout, call `chatgpt_last_response()`
instead of submitting the prompt again.

### Review contract

Include this contract in every request where the result will influence a code
change:

```text
Act as a read-only code reviewer and advisor. Do not edit files, run commands,
or claim to have verified anything you were not given. Identify only concrete,
actionable findings. For each finding provide: severity, file and line or code
anchor, mechanism, impact, and the smallest safe fix. Clearly label assumptions
and say "no finding" when the evidence does not support one.
```

If ChatGPT has a separate local connector, the same read-only instruction still
applies. That connector is outside this bridge and must not be assumed to be
available; include the important code or command output in the prompt whenever
the review needs to be reproducible.

### Common requests

Code review of a bounded change:

```text
Review the following diff as a read-only advisor. Check correctness, concurrency,
security, error handling, and test gaps. Prioritize only defects introduced or
affected by this change. Apply the review contract above.

Project rules:
<relevant rules>

Diff:
<git diff or selected file excerpts with line numbers>
```

Design or implementation decision:

```text
Act as a senior technical advisor. Compare options A and B for the stated goal.
State the recommendation first, then trade-offs, failure modes, and the minimum
validation plan. Do not edit code. Mark unknowns explicitly.

Goal: <goal>
Constraints: <constraints>
Evidence: <current architecture, measurements, or test output>
```

Sanity check before merging a fix:

```text
Perform a read-only adversarial review of this proposed fix. Try to falsify its
claim, identify regressions or missing edge cases, and list only checks that
would materially increase confidence. If it is sound, say so and explain why.

Bug mechanism: <mechanism>
Patch: <diff>
Verification already run: <commands and results>
```

### Calling the tools

Check readiness before a review:

```text
chatgpt_status()
```

Then send one bounded request. Six hundred seconds is the default because a
review that uses a separate local connector can take several minutes:

```text
chatgpt_ask(
  prompt="<review contract + focused context>",
  new_chat=true,
  timeout_seconds=600
)
```

Close the dedicated browser after the review:

```text
chatgpt_close()
```

Use 900 seconds only for a deliberately broad review. For ordinary questions,
keep the context small and the timeout at its default. A timeout is not a failed
review: wait briefly, then retrieve the latest answer with
`chatgpt_last_response()`.

## Usage and quota model

This bridge does not merge the ChatGPT and Codex accounts, API keys, or
contexts. In the intended setup, the browser session uses a normal ChatGPT Web
conversation, so the substantive reviewer answer is generated in ChatGPT Web's
conversation context and is subject to that surface's limits. Codex still uses
some of its own context and usage to formulate the MCP call and process the
returned review; this is not a free or unlimited path.

Keep this distinction in mind:

- Normal ChatGPT Web chat and Codex agent work are separate usage surfaces for
  this workflow, although both are governed by the same account and plan.
- ChatGPT Work and Codex share usage, credits, and limits. Do not assume the
  separation above applies when the browser conversation is actually using Work
  or another agentic feature.
- Tokens in prompts, file excerpts, tool results, and responses still count in
  the context where they are processed. A concise review prompt and a bounded
  response save usage on both sides.

The current official pricing documentation describes the shared Work/Codex
usage model and token accounting:

<https://learn.chatgpt.com/docs/pricing>

## Requirements

- macOS with Google Chrome
- Python 3.11
- A ChatGPT account that can be signed in through the visible browser window

The currently validated environment is:

```text
/opt/homebrew/anaconda3/envs/mlx/bin/python3
browser-use 0.12.9
mcp 1.26.0
```

Install dependencies into a Python 3.11 environment if needed:

```bash
python3.11 -m pip install -r requirements.txt
```

## First use

1. Start the MCP server or call `chatgpt_status` from Codex.
2. A dedicated visible Chrome window opens using
   `~/.codex-chatgpt/browser-profile`.
3. Sign in to ChatGPT manually. Never give credentials to Codex or the tool.
4. Call `chatgpt_status` again; it should report `ready`.

Override the profile location only when necessary:

```bash
export CODEX_CHATGPT_PROFILE_DIR="$HOME/.codex-chatgpt/browser-profile"
```

Keep the profile outside this repository. It contains authenticated browser
state and must never be committed or shared.

The browser remains visible for manual login and human-verification challenges,
but opens as a small window by default (`760x560` at position `24,60`) instead
of maximized. Adjust it without changing the code when needed:

```bash
export CODEX_CHATGPT_WINDOW_WIDTH=900
export CODEX_CHATGPT_WINDOW_HEIGHT=700
export CODEX_CHATGPT_WINDOW_X=100
export CODEX_CHATGPT_WINDOW_Y=120
```

The position uses the top-left origin and the size/position values are passed to
`browser-use`'s native headed Chrome window. Restart the MCP process after
changing these variables.

The default profile path and `CODEX_CHATGPT_*` variable prefix are legacy
runtime identifiers retained so an existing signed-in profile and local setup
continue to work after this project rename.

## Test

Run the deterministic unit tests and a real MCP initialization/list-tools
handshake without opening Chrome:

```bash
/opt/homebrew/anaconda3/envs/mlx/bin/python3 -m unittest discover -s tests -v
```

A live test should be deliberately initiated after manual login, for example by
asking ChatGPT to reply with exactly `PONG`. UI changes on chatgpt.com can break
selectors even when the unit and protocol tests still pass.

The bridge treats `auth.openai.com` as a login flow by URL and scopes signed-out
controls away from conversation content. It still depends on ChatGPT Web's
changing DOM and completion signals. A cancellation after a prompt has already
been submitted is not a rollback: ChatGPT may continue processing that request.

## Codex registration

Register the stdio server with absolute paths:

```bash
codex mcp add mcp-linkgpt -- \
  /opt/homebrew/anaconda3/envs/mlx/bin/python3 \
  /Users/champoomwat/Desktop/ENDEAVOR_AGENTIC/MCP-LinkGPT/server.py
```

Inspect or remove it with:

```bash
codex mcp get mcp-linkgpt
codex mcp remove mcp-linkgpt
```

Restart Codex after registration if the tool list does not refresh in the
current session.
