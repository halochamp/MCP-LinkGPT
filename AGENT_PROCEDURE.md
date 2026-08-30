# MCP-LinkGPT — Agent Procedure

This document is the **complete repository-agent operating procedure** for working directly inside the public `MCP-LinkGPT` project.

It is intentionally self-contained. Do not assume the agent can read another private repository, a parent workspace, or external project rules.

Document roles:

- [`CLAUDE.md`](CLAUDE.md) — hard project constraints that must always hold.
- [`AGENT.md`](AGENT.md) — short overview and decision map.
- **This file** — full execution procedure for an agent working in this repository.
- [`README.md`](README.md) — product behavior, MCP tool contract, browser lifecycle, setup, examples, and user documentation.

When instructions conflict, follow the most restrictive applicable rule. Never weaken the safety or ownership guarantees described in `CLAUDE.md` or `README.md`.

---

## 1. Core operating model

MCP-LinkGPT is a deterministic bridge that lets a calling agent consult ChatGPT Web as a **read-only reviewer or senior advisor**.

The calling agent remains responsible for:

1. reading the real local source and evidence;
2. deciding what question needs advice;
3. preparing a bounded prompt with the required evidence inline;
4. validating every material advisor claim against the local repository;
5. making edits itself;
6. running tests and verification itself; and
7. deciding when the work is complete.

ChatGPT Web is not the implementation worker and must never be treated as a substitute for local verification.

Mental model:

```text
local evidence
  -> focused LinkGPT question
  -> advisor answer
  -> local validation
  -> local edit/test
  -> same-thread recheck when needed
  -> closure
```

---

## 2. Start-of-task procedure

Before changing this repository:

1. Read [`CLAUDE.md`](CLAUDE.md).
2. Read [`AGENT.md`](AGENT.md) for the quick decision map.
3. Read the relevant section of this procedure.
4. Read the relevant section of [`README.md`](README.md) when the task touches MCP tool behavior, browser lifecycle, correlation, recovery, privacy, setup, or registration.
5. Inspect the current Git status before editing so unrelated work is not accidentally included.
6. Identify whether the task is:
   - documentation-only;
   - deterministic code/test maintenance;
   - browser lifecycle/correlation/recovery work;
   - MCP schema/tool-description work;
   - setup/registration work; or
   - an advisory/review workflow task.
7. Define the smallest verifiable goal before editing.

Do not start by broadly changing several files. Prefer one coherent change with a clear verification boundary.

---

## 3. Hard project boundaries

These constraints always apply:

- Keep MCP-LinkGPT independent from `Endeavor_Hands` or any other local connector project.
- Do not import, modify, or depend on another private repository to make this project work.
- Do not add an autonomous browser agent.
- Do not add a second browser LLM.
- Browser operations must remain deterministic and bounded.
- Never read or export browser cookies, passwords, authentication tokens, or storage through browser APIs. Prompts and responses may be processed transiently only as required for the intended MCP request/response, progress, and correlation flow; never persist them to application logs, commit them, or expose them to unrelated destinations.
- Keep the dedicated browser profile outside this repository.
- Restrict automatic navigation to the approved ChatGPT/OpenAI origins documented by the project.
- Never bypass CAPTCHA, login, human-verification, subscription, or other access gates.
- Review/advisor behavior is read-only. The calling agent owns all implementation.

If a proposed change weakens any of these boundaries, stop and redesign it.

---

## 4. User-triggered LinkGPT rule

The LinkGPT advisor workflow is **user-triggered**.

Use MCP-LinkGPT when the user explicitly asks to:

- use LinkGPT or MCP-LinkGPT;
- consult ChatGPT;
- get a ChatGPT second opinion;
- run a review/audit through ChatGPT Web; or
- continue an existing LinkGPT review thread.

Do not invoke LinkGPT merely because another opinion might be useful.

If the user requests an advisor but explicitly names a different advisor/provider, follow that request instead of silently substituting LinkGPT.

If the user has already requested LinkGPT for the current issue, subsequent mid-work and closure calls for that same issue are part of the same authorized workflow; the user does not need to repeat the command for every follow-up.

---

## 5. Advisor checkpoints

Once the user has requested LinkGPT, use advisor calls at **evidence-triggered checkpoints**, not reflexively.

### 5.1 Before implementation

First perform the smallest direct local check that could settle the uncertainty.

Consult LinkGPT before committing to a design when, after that check:

- two or more plausible approaches remain with non-obvious trade-offs;
- an unfamiliar lifecycle, API, protocol, or ownership contract materially affects correctness;
- the proposed fix depends on an assumption that has not been verified; or
- the calling agent cannot state how the proposed fix will be falsified.

Do not call an advisor merely to confirm an obvious mechanical edit.

### 5.2 Mid-work

Pause before another material change and consult LinkGPT when new evidence weakens the current plan, especially when:

- a targeted test or runtime observation contradicts the current root-cause hypothesis;
- the first fix fails and the next change would rely on a new unproven hypothesis;
- the fix unexpectedly expands into another component or ownership boundary;
- concurrency, cancellation, authentication, security, privacy, persistence, migration, data-loss, or difficult-to-reverse behavior unexpectedly becomes relevant;
- two reasonable next fixes remain after one direct local check;
- local evidence is internally inconsistent and one targeted check cannot reconcile it; or
- continuing would require guessing about behavior that materially affects correctness or validation.

### 5.3 Before completion

When the user requested an audit/review workflow, or when the requested workflow explicitly includes a final audit, perform a closure audit after local verification.

Ask ChatGPT to:

- try to falsify the implemented result;
- identify concrete residual risks;
- identify invariant breaks or missing edge cases;
- identify the smallest missing validation; and
- say clearly when the supplied evidence supports no remaining finding.

An earlier design or mid-work consultation does not replace a requested closure audit.

---

## 6. Anti-loop rule

Do not chain advisor calls without local work between them.

Every follow-up must contain at least one of:

- new evidence;
- updated code or diff;
- a new deterministic test result;
- an exact new runtime observation; or
- one newly narrowed unresolved question.

Do not resend the same question unchanged.

If two consecutive advisor turns on the same issue do not materially reduce uncertainty:

1. stop the advisor loop;
2. perform a new local check or narrow the scope;
3. re-frame the decision from concrete evidence; or
4. ask the user for direction when the remaining choice is genuinely ambiguous.

Advisor repetition is not a substitute for evidence.

---

## 7. Evidence package for code review

For any review that may influence code, send the **actual relevant code or diff inline**.

Valid context includes:

- exact changed hunks;
- the smallest complete surrounding function/class needed to reason about the change;
- relevant callers or interfaces when control flow crosses a boundary;
- relevant tests;
- exact errors or deterministic observations;
- project rules that constrain the decision; and
- verification already performed.

Paths, filenames, line numbers, commit IDs, and anchors are provenance labels only. They never substitute for the code itself.

Never ask ChatGPT to:

- open local files;
- inspect a local path;
- use `Endeavor_Hands`;
- call another local MCP connector;
- reconstruct missing evidence through tools; or
- infer a code change from filenames or paths alone.

If required evidence is absent, narrow the question or report insufficient evidence.

Never include credentials, cookies, access tokens, private keys, session data, or unrelated private content.

---

## 8. Review contract

When an advisor answer may influence a code change, include the read-only review contract from [`README.md`](README.md#review-contract).

The important behavioral requirements are:

- advisor is read-only;
- no commands or edits;
- no claims of verification beyond supplied evidence;
- concrete actionable findings only;
- severity + code anchor + mechanism + impact + smallest safe fix;
- assumptions labeled explicitly; and
- `no finding` / `insufficient evidence` when the evidence does not support a claim.

The calling agent must still reproduce or validate each material finding locally before changing code.

---

## 9. Choosing `new_chat`

Treat each coherent issue/review as one conversation thread.

### Use `new_chat=true` when

- starting the first advisor call for a new review thread;
- reviewing unrelated evidence;
- scope has materially changed into a different issue;
- the user explicitly asks for an independent, fresh, or from-scratch audit; or
- prior reasoning must intentionally not influence the new review.

### Use `new_chat=false` when

- asking a follow-up about the immediately preceding review;
- checking whether a previous finding is fixed;
- providing an updated diff for the same issue;
- asking a mid-work uncertainty question about the same issue;
- asking ChatGPT to clarify or reconsider a prior finding; or
- performing the closure audit for the same issue when prior reasoning remains relevant.

Conversation history contains prior reasoning, **not the current local state**. Every `new_chat=false` follow-up must still include the actual updated code/diff and any new test or observation inline.

Do not open a new conversation just because a fix was made. A fix-and-recheck cycle should normally remain in the same thread.

---

## 10. Normal LinkGPT tool lifecycle

Use this sequence:

```text
chatgpt_status()
  -> ready
chatgpt_ask(..., new_chat=true or false)
  -> status="completed"
validate locally
  -> edit/test locally
optional same-thread follow-up
  -> new_chat=false
closure audit when requested
  -> new_chat=false if same issue
chatgpt_close()
```

### Readiness

Call `chatgpt_status()` once before the review thread needs the browser and wait for its final result.

- Continue only at `ready`.
- Stop for `login_required` or `challenge` so the user can act.
- Do not implement your own polling loop for transient `loading`; the bridge owns readiness waiting.
- Treat readiness timeout or uncertain shutdown as a failed/incomplete advisory start.

### While `chatgpt_ask()` is running

- Remain in the same call until it returns.
- Do not call another browser tool concurrently.
- Progress notifications and visible response tails are provisional context only.
- Never treat a partial tail as final advice.
- Continue only after `status="completed"` and the confirmed final response are returned.

### Browser lifetime

Keep the dedicated browser open while the same issue/review thread remains active so `new_chat=false` retains the intended conversation.

Close it after:

- the closure audit completes;
- the review thread is intentionally finished;
- bounded timeout recovery is exhausted; or
- an ambiguous failed review is abandoned.

Never close the browser while `chatgpt_ask()` is running.

---

## 11. Timeout recovery

A timeout is special because the submitted ChatGPT response may still be running.

If `chatgpt_ask()` times out:

1. do **not** resubmit the prompt;
2. wait a short bounded interval between recovery checks;
3. call `chatgpt_last_response()` only for this timeout-recovery purpose;
4. accept the result only when it reports `completed`;
5. stop when the chosen bounded recovery budget is exhausted; and
6. report that the advisory pass did not complete if a completed answer cannot be confirmed.

Do not turn timeout recovery into an unbounded polling loop.

---

## 12. Ambiguous post-submit errors

Treat non-timeout post-submit ownership errors as fundamentally different from timeouts.

Examples include user-turn mismatch, conversation drift, target drift, page identity change, or another error where response ownership cannot be proven.

For these errors:

1. discard partial response tails;
2. do not use `chatgpt_last_response()` as advice;
3. do not retry the prompt automatically;
4. do not open a fresh chat automatically;
5. close the browser only after the failed call has returned; and
6. report that the advisory pass did not complete.

A new attempt requires explicit user direction because the original submission may have produced an answer whose ownership cannot be safely correlated.

---

## 13. Correlation warnings

A successful completed response may still report a rendering correlation fallback.

When `correlation_status` is `rendering_fallback`:

- the answer may be usable under the bridge's structural ownership guarantees;
- retain the accompanying `correlation_warning` when the distinction matters;
- do not describe the result as exact rendered-text correlation; and
- use `strict_user_turn_text=true` only when the caller requires rendered-text mismatch to be a hard failure.

Conversation, document, target, or user-turn-count ownership failures remain fail-closed.

---

## 14. Working on MCP-LinkGPT itself

Editing this project has an important lifecycle trap: a host-owned stdio MCP process does **not** hot-reload when source files change.

Therefore, when changing `server.py`, `chatgpt_web.py`, or other runtime behavior:

1. identify whether the current registered MCP process was started before the edit;
2. do not assume a successful source-level test means the currently registered tool is running the new code;
3. do not kill the host-owned registered stdio server from inside the same host expecting automatic reconnection;
4. use an isolated working-copy or direct test harness for development validation when appropriate;
5. close any isolated browser/session afterward;
6. ask the user to restart/re-enter the host when a live registered-tool acceptance test is required; and
7. after restart, re-run one bounded registered `chatgpt_status()` plus the relevant `chatgpt_ask()` acceptance check before claiming the live registered path is verified.

Clearly distinguish:

- source/test verification;
- isolated bridge verification; and
- registered-host live verification.

Never claim one proves another when the runtime process was not reloaded.

---

## 15. Code-change procedure

For production code changes:

1. state the exact invariant or bug being changed;
2. inspect the smallest complete code path involved;
3. prefer the smallest coherent fix;
4. preserve existing safety, ownership, correlation, and cleanup guarantees;
5. add or update deterministic regression coverage for the changed behavior;
6. run targeted tests first when useful;
7. run the full required test suite before completion:

```bash
python3 -m unittest discover -s tests -v
```

8. inspect the final diff for accidental unrelated edits;
9. verify no secrets, profile data, logs, cookies, prompts, or responses were added; and
10. report exactly what was and was not verified.

Do not weaken a test simply to make a new implementation pass.

---

## 16. Tool schema and description changes

Tool descriptions are model-facing behavior. Treat them as part of the product contract.

When changing a tool signature, default, description, or MCP-visible behavior:

- check backward compatibility;
- ensure `new_chat`, timeout, strict correlation, and recovery semantics remain unambiguous;
- update README examples and workflow text when necessary;
- update or add MCP handshake/schema tests; and
- run the full unit suite.

Do not hide a behavioral change only in prose if the runtime tool description would still guide agents incorrectly.

---

## 17. Documentation-only changes

For documentation-only work:

- keep `AGENT.md` concise and operational;
- put detailed agent execution rules in this file;
- put product/tool behavior and setup details in `README.md`;
- keep hard project constraints in `CLAUDE.md`;
- avoid copying large blocks between documents;
- link to the authoritative section instead;
- verify local Markdown links and heading anchors; and
- do not import private-only workflows or user-specific rules into the public repository.

A bounded documentation edit normally does not require browser or runtime testing unless it changes instructions that describe actual behavior and the behavior itself is uncertain.

---

## 18. Git discipline

Before commit or push:

1. inspect `git status`;
2. inspect the final diff;
3. stage only intended files;
4. do not include local editor settings, browser profiles, logs, caches, generated files, or unrelated work;
5. use a focused commit message; and
6. never force-push unless the user explicitly requires it and repository policy permits it.

Do not commit merely because files changed. Commit/push only when the user requests it or the active workflow explicitly authorizes it.

---

## 19. Completion criteria

A task is complete only when all applicable items are true:

- the requested change is implemented or documentation is updated;
- the local evidence supports the stated result;
- relevant tests/checks pass;
- advisor findings that influenced the change were reproduced or validated locally;
- requested closure audit is complete;
- no unresolved ambiguous browser ownership remains;
- the dedicated browser is closed when the review thread is finished;
- the final diff contains only intended changes; and
- the final report distinguishes verified facts from unverified assumptions.

If a required LinkGPT audit could not complete, say so explicitly rather than claiming full completion.

---

## 20. Compact decision table

| Situation | Action |
|---|---|
| User did not request LinkGPT/ChatGPT review | Work locally; do not invoke LinkGPT automatically |
| User requests LinkGPT / ChatGPT second opinion | Start LinkGPT workflow |
| Obvious mechanical edit | Local work; advisor usually unnecessary |
| Two plausible designs remain after one direct check | Ask advisor before implementation |
| Test contradicts current hypothesis | Pause; gather minimal new evidence; ask same-thread advisor if still uncertain |
| Fix applied to same issue | Recheck with `new_chat=false` and updated diff/evidence |
| User requests fresh independent audit | `new_chat=true` |
| `chatgpt_ask` timeout | Do not resend; bounded `chatgpt_last_response()` recovery |
| Non-timeout ownership/correlation error | Discard partial output; no retry; require explicit user direction |
| Same review thread still active | Keep browser open |
| Thread complete/abandoned | Close browser |
| Runtime source changed | Tests do not prove registered host reloaded; perform restart + registered acceptance when required |
| Advisor gives a finding | Validate locally before editing |
| Two advisor turns fail to reduce uncertainty | Stop loop; narrow locally or ask user |

---

## 21. What to read next

For normal repository work:

1. [`AGENT.md`](AGENT.md) — quick overview.
2. This procedure — full agent workflow.
3. [`README.md`](README.md) — exact product/tool/browser contract.
4. [`CLAUDE.md`](CLAUDE.md) — hard project constraints.

For LinkGPT calls specifically, read the current README sections covering:

- `When to invoke LinkGPT`;
- `Advisor checkpoints`;
- `Choosing new_chat`;
- `Review contract`; and
- `Calling the tools`.

For runtime modifications, also read the README's browser safety, ownership, correlation, timeout, and recovery sections before changing behavior.
