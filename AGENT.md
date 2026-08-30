# MCP-LinkGPT — Agent Quick Workflow

This file is the **quick operational overview for an agent**. It is intentionally short.
The full contract, safety model, recovery behavior, examples, installation, and implementation details are in [`README.md`](README.md).

## 1. Use LinkGPT only when the user asks

MCP-LinkGPT is a **user-triggered advisor workflow**.

Use it when the user explicitly asks to:

- use LinkGPT / MCP-LinkGPT;
- consult ChatGPT or get a ChatGPT second opinion;
- run an audit/review through ChatGPT Web; or
- continue a LinkGPT review already in progress.

Do **not** invoke LinkGPT automatically just because another opinion might help.

Full rule: [`README.md` — When to invoke LinkGPT](README.md#when-to-invoke-linkgpt).

## 2. The calling agent remains the worker

ChatGPT Web is a **read-only reviewer/advisor**, not the implementation worker.

The calling agent must:

1. inspect the real local evidence;
2. prepare the focused question and inline evidence;
3. ask ChatGPT through MCP-LinkGPT;
4. validate every material recommendation locally;
5. make the actual edits and run tests itself.

Never treat advisor output as locally verified fact.

## 3. Advisor decision checkpoints

After the user has requested LinkGPT, use this ordered rule:

### Before implementation

First perform the smallest direct local check that could settle the question.

Consult LinkGPT if material uncertainty remains, especially when:

- two or more plausible approaches remain with non-obvious trade-offs;
- an unfamiliar lifecycle/API contract is important to correctness; or
- you cannot explain how the proposed fix will be falsified.

### Mid-work

Pause before another material change and consult LinkGPT when new evidence weakens the current plan, for example:

- a targeted test contradicts the current hypothesis;
- the first fix fails and the next step depends on a new unproven hypothesis;
- scope unexpectedly expands across a high-risk boundary;
- two reasonable next fixes remain after one direct local check; or
- local evidence is still internally inconsistent.

### Before completion

If the user requested an audit workflow, or the task requires a final audit, run a closure review after local verification. Ask ChatGPT to try to falsify the result, identify residual risks, and name the smallest missing validation.

An earlier consultation does not replace a requested final closure audit.

Do not chain advisor calls without local work between them. Every follow-up must contain new evidence, updated code/diff, or one concrete unresolved question. If two consecutive advisor turns on the same issue do not reduce uncertainty, stop the loop and narrow/re-frame the question or ask the user for direction.

Full rule: [`README.md` — Advisor checkpoints](README.md#advisor-checkpoints).

## 4. Conversation rule: `new_chat`

Use `new_chat=true` for:

- the first review in a thread;
- unrelated evidence or a materially different scope; or
- an explicitly independent / fresh / from-scratch audit.

Use `new_chat=false` for the **same issue/review thread**, including:

- follow-up questions;
- checking whether a previous finding is fixed;
- mid-work uncertainty questions;
- fix-and-recheck turns; and
- the closure audit when it depends on the same prior reasoning.

Conversation history contains prior reasoning, **not the new code**. Every follow-up must still include the actual updated code/diff, relevant test result, or exact new observation inline.

Keep the dedicated browser open while the same review thread is active so `new_chat=false` retains the intended conversation. Close it after the thread is complete or abandoned.

Full rule: [`README.md` — Choosing `new_chat`](README.md#choosing-new_chat).

## 5. Evidence contract

For code review, send the **actual relevant code or diff inline**.

Paths, filenames, line numbers, and anchors are provenance labels only. Do not ask ChatGPT to fetch omitted local files, use another connector, or infer a change from a path alone.

Never send credentials, cookies, tokens, private keys, or unrelated private content.

Before a code-changing review, use the full read-only review contract from [`README.md` — Review contract](README.md#review-contract).

## 6. Tool flow

Normal flow:

```text
chatgpt_status()
  -> ready
chatgpt_ask(...)
  -> status="completed"
validate advice locally
  -> edit/test locally
optional same-thread follow-up with new_chat=false
  -> closure audit when requested
chatgpt_close()
```

Important rules:

- Call `chatgpt_status()` once and continue only when it returns `ready`.
- Do not call another browser tool while `chatgpt_ask()` is running.
- Progress tails are provisional context, not final advice.
- Accept advisor output only after `status="completed"`.
- If `chatgpt_ask()` times out, do **not** resend the prompt; use bounded `chatgpt_last_response()` recovery and accept only `completed`.
- For other ambiguous post-submit errors, discard partial output and do not retry or open a new chat automatically. Follow the README recovery contract.

Full tool and recovery rules: [`README.md` — Calling the tools](README.md#calling-the-tools).

## 7. Read the full README when needed

Before the first LinkGPT use in a work session, read at least these sections:

1. [`When to invoke LinkGPT`](README.md#when-to-invoke-linkgpt)
2. [`Advisor checkpoints`](README.md#advisor-checkpoints)
3. [`Choosing new_chat`](README.md#choosing-new_chat)
4. [`Review contract`](README.md#review-contract)
5. [`Calling the tools`](README.md#calling-the-tools)

Read the full [`README.md`](README.md) when setup, browser safety, privacy, lifecycle, timeout/recovery, installation, or implementation details matter.

**Mental model:** local evidence -> LinkGPT advice -> local verification -> local action -> re-check when needed.
