# MCP-LinkGPT — Agent Overview

This file is the **quick operating map for an agent opened directly inside this public repository**.

This project is self-contained. Do **not** assume access to a private parent repository or external agent procedure.

Read order:

1. [`CLAUDE.md`](CLAUDE.md) — hard project constraints.
2. **This file** — quick workflow and decision points.
3. [`AGENT_PROCEDURE.md`](AGENT_PROCEDURE.md) — full repository-agent procedure.
4. [`README.md`](README.md) — exact MCP-LinkGPT product/tool/browser contract, setup, and examples.

## Core model

MCP-LinkGPT lets a calling agent consult ChatGPT Web as a **read-only reviewer/advisor**.

The calling agent remains the worker and owns:

- local evidence gathering;
- implementation decisions;
- edits;
- commands/tests;
- verification; and
- the final answer.

Never treat advisor output as locally verified fact.

## Start every task

Before editing:

1. read `CLAUDE.md`;
2. inspect Git status;
3. classify the task and define the smallest verifiable goal;
4. read the relevant section of `AGENT_PROCEDURE.md`; and
5. read the relevant `README.md` section when the task touches tool behavior, browser lifecycle, correlation, recovery, privacy, setup, or registration.

Full procedure: [`AGENT_PROCEDURE.md` — Start-of-task procedure](AGENT_PROCEDURE.md#2-start-of-task-procedure).

## Use LinkGPT only when the user asks

LinkGPT is a **user-triggered advisor workflow**.

Use it when the user explicitly asks to:

- use LinkGPT / MCP-LinkGPT;
- consult ChatGPT;
- get a ChatGPT second opinion;
- run an audit/review through ChatGPT Web; or
- continue an existing LinkGPT review thread.

Do not invoke it automatically just because another opinion might help.

Once the user has requested LinkGPT for an issue, same-issue follow-ups and closure checks remain part of that authorized workflow.

Full rule: [`AGENT_PROCEDURE.md` — User-triggered LinkGPT rule](AGENT_PROCEDURE.md#4-user-triggered-linkgpt-rule).

## Advisor checkpoints

After LinkGPT is authorized, use it **when evidence triggers a decision point**, not reflexively.

### Before implementation

Do one smallest direct local check first. Ask the advisor if material uncertainty remains, especially when multiple plausible designs, unfamiliar contracts, or an unverified assumption materially affect correctness.

### Mid-work

Pause before another material change when new evidence weakens the plan: a test contradicts the hypothesis, the first fix fails, scope expands across a high-risk boundary, two plausible next fixes remain, or local evidence is still inconsistent.

### Before completion

If the requested workflow includes a final audit, perform a closure review after local verification. Earlier consultations do not replace it.

Do not chain advisor calls without new local work/evidence. If two consecutive advisor turns do not reduce uncertainty, stop the loop and narrow locally or ask the user.

Full rule: [`AGENT_PROCEDURE.md` — Advisor checkpoints](AGENT_PROCEDURE.md#5-advisor-checkpoints).

## Conversation rule: `new_chat`

Use `new_chat=true` for:

- the first call in a new review thread;
- unrelated/materially different scope; or
- an explicitly independent/fresh/from-scratch audit.

Use `new_chat=false` for the same issue/review thread:

- follow-up questions;
- checking a previous finding after a fix;
- mid-work uncertainty;
- fix-and-recheck; and
- same-thread closure audit.

History carries previous reasoning, **not new code**. Every follow-up must still include the actual updated code/diff and new tests/observations inline.

Full rule: [`AGENT_PROCEDURE.md` — Choosing new_chat](AGENT_PROCEDURE.md#9-choosing-new_chat).

## Evidence rule

For code review, send the **actual relevant source or diff inline**.

Paths, line numbers, commit IDs, and anchors are provenance only. Never ask ChatGPT to fetch omitted local files, use another connector, or infer changes from paths.

Never send credentials, cookies, tokens, private keys, session data, or unrelated private content.

Full rule: [`AGENT_PROCEDURE.md` — Evidence package](AGENT_PROCEDURE.md#7-evidence-package-for-code-review).

## Normal tool lifecycle

```text
chatgpt_status()
  -> ready
chatgpt_ask(...)
  -> status="completed"
validate advisor claims locally
  -> edit/test locally
same-thread follow-up when needed
  -> new_chat=false
closure audit when requested
  -> new_chat=false if same issue
chatgpt_close()
```

Key rules:

- `chatgpt_status()` owns readiness waiting; continue only at `ready`.
- Stop for login/challenge states.
- Never call another browser tool while `chatgpt_ask()` is running.
- Progress tails are provisional, not final advice.
- Accept advice only after `status="completed"`.
- Keep the browser open while the same review thread is active.
- Close it when the thread completes, recovery is exhausted, or the review is abandoned.

Full rule: [`AGENT_PROCEDURE.md` — Normal LinkGPT tool lifecycle](AGENT_PROCEDURE.md#10-normal-linkgpt-tool-lifecycle).

## Recovery rule

### Timeout

Do **not** resend the prompt. Use bounded `chatgpt_last_response()` recovery and accept only `completed`.

### Other post-submit ownership/correlation error

Discard partial output. Do not use `chatgpt_last_response()` as advice, do not retry automatically, and do not open a fresh chat automatically. Report the incomplete advisory pass and require explicit user direction for a new attempt.

Full rules:

- [`AGENT_PROCEDURE.md` — Timeout recovery](AGENT_PROCEDURE.md#11-timeout-recovery)
- [`AGENT_PROCEDURE.md` — Ambiguous post-submit errors](AGENT_PROCEDURE.md#12-ambiguous-post-submit-errors)

## When modifying MCP-LinkGPT itself

A host-owned stdio MCP process does **not** hot-reload source changes.

Therefore:

- source/unit tests do not prove the currently registered tool is running new code;
- do not kill the registered stdio server expecting the current host to reconnect;
- use isolated development validation when appropriate;
- restart/re-enter the host when a registered live acceptance test is required; and
- distinguish source/test verification, isolated bridge verification, and registered-host verification.

Full rule: [`AGENT_PROCEDURE.md` — Working on MCP-LinkGPT itself](AGENT_PROCEDURE.md#14-working-on-mcp-linkgpt-itself).

## Verification

After production code changes, run:

```bash
python3 -m unittest discover -s tests -v
```

Inspect the final diff, preserve safety/ownership/correlation guarantees, and never weaken tests merely to make a change pass.

Full rule: [`AGENT_PROCEDURE.md` — Code-change procedure](AGENT_PROCEDURE.md#15-code-change-procedure).

## Documentation structure

Keep responsibilities separate:

- `CLAUDE.md` = hard constraints
- `AGENT.md` = quick overview
- `AGENT_PROCEDURE.md` = full agent workflow
- `README.md` = product/tool/browser behavior and user documentation

Do not duplicate the full procedure across files; link to the authoritative section.

## Completion gate

Before saying done, verify applicable items:

- requested change is complete;
- relevant tests/checks passed;
- advisor findings used in the change were validated locally;
- requested closure audit completed;
- no ambiguous browser ownership remains;
- browser is closed when the review thread is finished;
- final diff contains only intended changes; and
- final response distinguishes verified facts from assumptions.

Full gate: [`AGENT_PROCEDURE.md` — Completion criteria](AGENT_PROCEDURE.md#19-completion-criteria).

**Mental model:** local evidence -> LinkGPT advice -> local verification -> local action -> re-check -> closure.
