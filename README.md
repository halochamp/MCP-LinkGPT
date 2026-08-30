# MCP-LinkGPT

[English](#english) | [ภาษาไทย](#ภาษาไทย)

## English

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

- `chatgpt_status()` opens the dedicated browser and waits internally for the
  ChatGPT composer to become usable. Transient `loading` is not returned as a
  final tool result. One 30-second readiness budget starts before browser
  startup and covers startup, navigation, and page initialization. The call
  returns `ready`, `login_required`, or `challenge`; if readiness times out
  before any prompt is sent, the bridge closes the browser and returns a
  readiness failure. If shutdown also fails, that readiness failure is
  preserved and reports that session ownership remains uncertain.
- `chatgpt_close()` closes the dedicated browser session without sending or
  reading conversation content. The MCP registration remains available, and
  the next browser tool call can start a new session.
- `chatgpt_new_chat()` opens a fresh ChatGPT conversation.
- `chatgpt_last_response()` reads the latest assistant response without sending
  another prompt and labels it `in_progress` or `completed`. Use it only for
  bounded recovery after an explicit `chatgpt_ask` timeout, not after an
  ambiguous non-timeout error.
- `chatgpt_ask(prompt, new_chat=true, timeout_seconds=600)` sends one prompt,
  reports progress notifications, including a bounded tail of the visible
  response while it is generating. Progress values never decrease; if ChatGPT
  temporarily hides a response turn after generation started, the bridge keeps
  the response phase instead of reporting that generation never began. The call
  then returns the confirmed final response with `status: "completed"` and an
  explicit completion message. Post-submit rendered-text correlation is
  advisory by default because the ChatGPT UI representation can change. A
  mismatch may still complete with `correlation_status: "rendering_fallback"`
  and an explicit `correlation_warning`; the answer is usable but
  lower-confidence. Set `strict_user_turn_text=true` when any rendered-text
  mismatch must fail instead.

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
that prompt before accepting a new assistant response. Normalization changes
only line endings and ChatGPT's NBSP representation; indentation, repeated
spaces, tabs, blank lines, and line boundaries remain significant. The only
additional exact forms are explicitly observed inline-code and paired
fenced-code presentation transformations. If rendered text still differs, the
default structural ownership fallback continues only because the bridge already
verified the exact composer atomically before clicking Send and still requires
exactly one new user turn plus the bound conversation, document, and browser
target. General fuzzy similarity is never used. The result reports structural,
content-free mismatch metadata in `correlation_warning`. Count, conversation,
document, and target mismatches always fail closed. Set
`strict_user_turn_text=true` to make post-submit rendered text a hard gate.

This MCP is directional: Codex can call ChatGPT Web, but ChatGPT Web cannot call
back through this server to read arbitrary local paths. ChatGPT Web may still
have a separate user-installed local connector, such as
`Endeavor_Hands`; that is an independent tool route with its own access
policy. Reviews that use such a connector can take several minutes, so keep the
default 600-second timeout or raise it to at most 900 seconds when necessary.

## LinkGPT review and advisory workflow

### When to invoke LinkGPT

This is a **user-triggered workflow**. The calling agent should use MCP-LinkGPT
when the user explicitly asks to use LinkGPT, consult ChatGPT, get a ChatGPT
second opinion, run an audit/review through ChatGPT Web, or continue a LinkGPT
review already in progress. Do not invoke LinkGPT merely because a task could
benefit from another opinion.

Once the user has requested LinkGPT, use it as a read-only reviewer or senior
advisor, not as the autonomous implementation worker. The normal hand-off is:

1. The calling agent reads the relevant files, diff, test output, and project
   rules, then puts the actual code/diff and necessary evidence inline in the
   prompt.
2. The calling agent sends ChatGPT a focused prompt; paths and line anchors are
   provenance only and never substitute for the inline evidence.
3. ChatGPT returns findings, risks, alternatives, or a recommended decision.
4. The calling agent validates every material claim against the real codebase.
5. The calling agent implements only the changes that are in scope, runs the
   relevant tests, and reports the result.

### Advisor checkpoints

Once the user has requested LinkGPT, use these checkpoints as an ordered
decision rule. Advisor use should be **evidence-triggered, not reflexive**:
perform the smallest direct local check that could settle the doubt first. If
that check resolves the issue, continue locally. If material uncertainty
remains, consult ChatGPT before guessing or expanding the change.

1. **Before implementation — consult when the plan itself is uncertain.** Use
   LinkGPT after one local evidence pass when two or more plausible approaches
   remain with non-obvious trade-offs, when an unfamiliar lifecycle/API
   contract is material to correctness, or when the calling agent cannot state
   how the proposed fix will be falsified. Do not call merely to confirm an
   obvious mechanical edit.
2. **Mid-work — consult when new evidence weakens the current plan.** Pause
   before another material change when a targeted test contradicts the current
   hypothesis, the first fix fails and the next step depends on a new unproven
   hypothesis, scope unexpectedly expands across a high-risk boundary, two
   reasonable next fixes remain after one direct check, or local evidence is
   still internally inconsistent.
3. **Before completion — use a closure audit when the user requested an audit
   workflow or when the task needs one.** Ask ChatGPT to try to falsify the
   implemented result, identify residual risks, and name the smallest missing
   validation. Earlier consultations do not replace this closure check when a
   final audit is part of the requested workflow.

Do not chain advisor calls without local work between them. Every follow-up must
carry new evidence, updated code/diff, or a concrete unresolved question. If two
consecutive advisor turns on the same issue do not reduce uncertainty, stop the
loop, narrow or re-frame the question from local evidence, or ask the user for
direction instead of repeatedly consulting.

### Choosing `new_chat`

Use `new_chat=true` when:

- starting an independent review;
- reviewing unrelated evidence;
- intentionally requesting a fresh opinion that must not depend on the previous answer;
- the user says `independent`, `fresh audit`, `from scratch`, or equivalent wording.

Use `new_chat=false` when:

- asking a follow-up about the immediately preceding review;
- verifying that a previous finding has been fixed;
- providing updated code or a diff for the same issue;
- asking ChatGPT to reconsider, clarify, or re-audit its previous finding;
- the prompt refers to `your previous finding`, `the previous audit`, `fixed X`, `re-audit this fix`, or equivalent wording.

For a normal fix-and-recheck loop, keep the same conversation:

1. Initial audit: call `chatgpt_ask(..., new_chat=true)`.
2. ChatGPT reports finding X.
3. Fix X locally.
4. Recheck the fix with `chatgpt_ask(prompt="<finding X was fixed + actual updated code/diff inline>", new_chat=false)`.
5. Repeat steps 2-4 while closing findings from that same review.
6. After all findings are closed, use `new_chat=true` only if the user requests, or the task requires, a final independent audit.

Even with `new_chat=false`, always include the actual updated code or diff inline.
Conversation history supplies the previous reasoning, not the new code. Reuse the
same conversation for mid-work questions, fix-and-recheck turns, and closure
audits that concern the same issue/review thread. Never ask ChatGPT to fetch
local files or infer changes from file paths alone.

If the response is still running after the timeout, call `chatgpt_last_response()`
instead of submitting the prompt again. Its returned status distinguishes a
still-running response from a completed one.

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

Check readiness once before a review. The bridge owns one 30-second readiness
budget starting before browser startup and covering navigation plus page
loading, so callers must not retry a transient loading state themselves.
Continue only at `ready`. Stop for `login_required` or `challenge` so the user
can act. A readiness timeout happens before prompt submission; the bridge
closes the browser before returning it. If shutdown also fails, the readiness
failure remains visible and session ownership stays uncertain:

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

During the wait, MCP clients that request progress receive lifecycle updates:
the question was sent, ChatGPT is waiting or generating, or the bridge is
confirming stability. While generating, an update includes the latest visible
response tail (up to 1,200 characters); it lets the calling agent understand
the current context before the final response arrives. Do not make another
browser call until `chatgpt_ask` returns. A successful result includes
`status: "completed"` and confirms that the final answer is ready to use. Keep
the dedicated browser open while the same issue/review thread is active so
`new_chat=false` follow-ups retain the intended conversation. Close it after
the closure audit, exhausted recovery, or an abandoned review thread.
When `correlation_status` is `rendering_fallback`, retain and disclose its
`correlation_warning` when the distinction matters; do not represent it as an
exactly correlated answer.

If `chatgpt_ask` times out, do not resubmit the prompt. Wait 2-5 seconds between
bounded `chatgpt_last_response()` polls and accept only `status: "completed"`.
For any other post-submit error—especially a user-turn, conversation, target,
or page-change error—response ownership is ambiguous: discard partial tails,
do not use `chatgpt_last_response()` as advice, do not retry or open a new chat
automatically, and report the failed review. A new attempt requires explicit
user direction.

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
  /absolute/path/to/MCP-LinkGPT/server.py
```

Codex applies its own per-tool deadline outside MCP-LinkGPT. Because this
bridge intentionally permits 600-900 second reviews, add this line inside the
generated `[mcp_servers.mcp-linkgpt]` table in `~/.codex/config.toml`:

```toml
tool_timeout_sec = 900.0
```

The host timeout must be at least as long as the largest
`chatgpt_ask(timeout_seconds=...)` value you intend to use. Restart Codex after
changing this setting. Otherwise the host can abandon the MCP request while the
bridge still owns the browser and is waiting for ChatGPT to finish.

Inspect or remove it with:

```bash
codex mcp get mcp-linkgpt
codex mcp remove mcp-linkgpt
```

Restart Codex after registration if the tool list does not refresh in the
current session.

---

## ภาษาไทย

`MCP-LinkGPT` คือ local MCP server ขนาดเล็กที่ให้ Codex ปรึกษา ChatGPT Web
ผ่าน Chrome profile เฉพาะ บทบาทของมันคือเป็น reviewer และ senior advisor แบบ
**อ่านอย่างเดียว** เพื่อให้ความเห็นที่สองเกี่ยวกับโค้ด การทดสอบ การออกแบบ และ
แนวทางแก้ไข ส่วนการรวบรวมหลักฐาน การตัดสินใจ การแก้โค้ด และการตรวจสอบผลลัพธ์
ยังเป็นความรับผิดชอบของ Codex

```text
Codex -> MCP-LinkGPT server -> browser-use/CDP -> ChatGPT Web
```

โปรเจกต์นี้ไม่ใช้ OpenAI API key ไม่รัน browser LLM ตัวที่สอง และไม่แก้ไข
`browser-use` การทำงานของ browser เป็นแบบ deterministic และจำกัดอยู่ที่โดเมน
ChatGPT/OpenAI ที่จำเป็นสำหรับเว็บไซต์และการเข้าสู่ระบบ

ชื่อโปรเจกต์และชื่อ MCP registration คือ `MCP-LinkGPT` / `mcp-linkgpt` ส่วน
ชื่อ Python ที่ขึ้นต้นด้วย `chatgpt_*` และ environment variables ที่ขึ้นต้นด้วย
`CODEX_CHATGPT_*` ยังคงเดิมเพื่อความเข้ากันได้ โดยเป็นชื่อของบริการที่เชื่อมต่อ
ไม่ใช่ชื่อที่แสดงของโปรเจกต์

### เครื่องมือ

- `chatgpt_status()` เปิด browser เฉพาะและรอภายในจนช่องเขียนข้อความพร้อมใช้
  โดยไม่คืน `loading` ชั่วคราวเป็นผลสุดท้าย มี readiness budget 30 วินาทีหนึ่ง
  ชุดครอบคลุมการเปิด browser, navigation และ page initialization แล้วคืน
  `ready`, `login_required` หรือ `challenge` หากหมดเวลาก่อนส่ง prompt bridge
  จะปิด browser และคืน readiness failure โดยยังรักษาสาเหตุเดิมไว้หากการปิด
  browser ล้มเหลวด้วย
- `chatgpt_close()` ปิด browser session โดยไม่ส่งหรืออ่านเนื้อหาการสนทนา การ
  ลงทะเบียน MCP ยังคงอยู่ และการเรียกครั้งถัดไปสามารถเปิด session ใหม่ได้
- `chatgpt_new_chat()` เปิดบทสนทนา ChatGPT ใหม่
- `chatgpt_last_response()` อ่านคำตอบล่าสุดโดยไม่ส่ง prompt ใหม่ และระบุสถานะ
  `in_progress` หรือ `completed` ใช้เฉพาะการกู้คืนแบบมีขอบเขตหลัง
  `chatgpt_ask` timeout อย่างชัดเจน ไม่ใช้หลัง error อื่นที่ระบุเจ้าของ response
  ไม่ได้
- `chatgpt_ask(prompt, new_chat=true, timeout_seconds=600)` ส่ง prompt หนึ่ง
  รายการ พร้อมส่ง progress และ tail ของคำตอบที่กำลังสร้าง ค่า progress จะไม่
  ลดลง หาก ChatGPT ซ่อน response turn ที่เริ่มสร้างแล้วชั่วคราว bridge จะคง
  สถานะ response ไว้แทนการรายงานว่ายังไม่เริ่มสร้างคำตอบ จากนั้นจึงคืนคำตอบ
  สุดท้ายที่ยืนยันว่าเสถียรแล้วพร้อม `status: "completed"` โดยค่าเริ่มต้น
  ความต่างของข้อความที่ UI render หลังส่งเป็นเพียงคำเตือน เพราะ representation
  ของ ChatGPT เปลี่ยนได้ หากต่างกันระบบยังคืนคำตอบพร้อม
  `correlation_status: "rendering_fallback"` และ `correlation_warning` ได้
  ใช้ `strict_user_turn_text=true` เมื่อต้องการให้ความต่างดังกล่าวเป็น error

มีเพียงหนึ่ง process เท่านั้นที่เป็นเจ้าของ profile เฉพาะได้ในเวลาเดียวกัน
ระบบไม่เขียน prompt หรือ response ลง application logs จะไม่พยายามข้าม CAPTCHA
หรือหน้าตรวจสอบมนุษย์ bridge จะตรวจ canonical path ของ profile และปฏิเสธ
profile ที่อยู่ใน repository รวมถึง symlink ที่ชี้เข้ามา ตรวจซ้ำก่อนใช้งาน ต้องมี
parent directory ที่เป็นของผู้ใช้และไม่เปิดให้ทุกคนเขียนได้ และเปิด lock โดยไม่
ตาม symlink

bridge ปิด extensions เริ่มต้นของ browser-use และ storage-state watchdog เพราะ
browser-use 0.12.9 จะคอยอ่าน cookie เมื่อใช้ persistent `user_data_dir` bridge
ไม่เรียก cookie API และไม่ export browser storage; profile มีไว้ให้ Chrome ใช้
กับ session ที่ผู้ใช้ลงชื่อเข้าใช้เท่านั้น

ทุก browser operation จะผูกงาน CDP กับ target เดียวและตรวจสอบ ChatGPT origin
ที่จุด mutation ของ DOM หากผู้ใช้เปลี่ยน tab หรือหน้าเว็บนำทางไปที่อื่น งานจะ
หยุดแบบ fail closed หากการเริ่มหรือปิด browser ถูกยกเลิก หรือยืนยันไม่ได้ว่า
browser หยุดแล้ว bridge จะคง session ownership และ profile lock ไว้ เขียน marker
`.unclean` และบล็อกการเชื่อมต่อใหม่ การ restart อย่างเดียวไม่ใช่การกู้คืน ต้อง
ยืนยันก่อนว่า browser หยุดแล้ว ลบ marker ตามชื่อที่ error ระบุ แล้วจึง restart MCP
process การยกเลิกหลังส่ง prompt แล้วไม่ใช่ rollback ดังนั้น request ถัดไปต้องรอ
ให้ cleanup ชัดเจน

ก่อนส่ง `chatgpt_ask` จะตรวจว่า composer ที่มองเห็นมี prompt ที่ normalize แล้ว
ตรงกันทุกตัวอักษรใน runtime evaluation เดียวกับการคลิก Send หลังส่งจะต้องพบ
user turn ใหม่เพียงหนึ่งรายการ โดยข้อความล่าสุดต้องตรงกับ prompt ที่ส่งก่อนรับ
assistant response การ normalize เปลี่ยนเฉพาะ line ending และ NBSP ของ ChatGPT
ส่วน indentation, repeated spaces, tabs, blank lines และ line boundaries ยังคงมี
ความหมาย การแสดงผล inline-code และ paired fenced-code ที่ยืนยันแล้วเท่านั้นจึง
ถือเป็นรูปแบบ exact ที่เทียบได้ หากข้อความที่ UI render ยังต่าง ระบบจะใช้
structural ownership fallback ได้เฉพาะเมื่อ bridge ตรวจ composer แบบ atomic ก่อน
คลิก Send แล้ว และยังตรวจ conversation, document, browser target และจำนวน
user turn อย่างเข้มงวด ระบบไม่ใช้ fuzzy similarity และคืน structural metadata ที่
ไม่เปิดเผยเนื้อหาใน `correlation_warning` ส่วน count, conversation, document และ
target mismatch ยังคง fail closed เสมอ หากต้องการให้ rendered text เป็น hard gate
ให้ตั้ง `strict_user_turn_text=true`

MCP นี้เป็น directional: Codex เรียก ChatGPT Web ได้ แต่ ChatGPT Web เรียกกลับ
ผ่าน server นี้เพื่ออ่าน path ในเครื่องแบบ arbitrary ไม่ได้ หากผู้ใช้ติดตั้ง
connector แยก เช่น `Endeavor_Hands` นั่นเป็นเส้นทางคนละตัวและมีนโยบาย
การเข้าถึงของตัวเอง การ review ที่ใช้ connector ดังกล่าวอาจใช้เวลาหลายนาที จึง
ควรใช้ timeout เริ่มต้น 600 วินาที หรือเพิ่มได้ไม่เกิน 900 วินาทีเมื่อจำเป็น

### LinkGPT workflow สำหรับ review และให้คำปรึกษา

#### เมื่อใดจึงควรเรียก LinkGPT

workflow นี้ทำงาน **เมื่อผู้ใช้สั่ง** ให้ใช้ LinkGPT เท่านั้น ตัว agent ควรเรียก
MCP-LinkGPT เมื่อผู้ใช้ระบุชัดเจนว่าให้ใช้ LinkGPT, ให้ปรึกษา ChatGPT, ขอ second
opinion จาก ChatGPT, ให้ audit/review ผ่าน ChatGPT Web หรือให้ทำต่อจาก LinkGPT
review ที่กำลังดำเนินอยู่ ไม่ควรเรียก LinkGPT เองเพียงเพราะคิดว่างานอาจได้
ประโยชน์จากความเห็นเพิ่มเติม

เมื่อผู้ใช้สั่งให้ใช้ LinkGPT แล้ว ให้ใช้ ChatGPT เป็น reviewer หรือ senior advisor
แบบ read-only ไม่ใช่ worker ที่แก้โค้ดแทน ขั้นตอนปกติคือ:

1. agent ที่เรียกอ่านไฟล์ diff ผลทดสอบ และกฎของโปรเจกต์ที่เกี่ยวข้อง แล้วใส่
   actual code/diff และหลักฐานที่จำเป็นลงใน prompt โดยตรง
2. agent ส่ง focused prompt ไปยัง ChatGPT โดย path และ line anchor ใช้เป็น
   provenance เท่านั้น ไม่ใช้แทนหลักฐานที่ต้องแนบ inline
3. ChatGPT ส่ง findings, ความเสี่ยง ทางเลือก หรือคำแนะนำกลับมา
4. agent ตรวจสอบ claim ที่สำคัญทุกข้อกับ codebase จริง
5. agent แก้เฉพาะสิ่งที่อยู่ในขอบเขต รันทดสอบที่เกี่ยวข้อง และรายงานผล

#### Advisor checkpoints

เมื่อผู้ใช้สั่งให้ใช้ LinkGPT แล้ว ให้ใช้ checkpoints ต่อไปนี้เป็น decision rule
ตามลำดับ การเรียก advisor ควรเป็นแบบ **evidence-triggered ไม่ใช่ reflexive**:
ทำ direct local check ที่เล็กที่สุดซึ่งอาจตอบข้อสงสัยได้ก่อน ถ้าตรวจแล้วจบให้ทำ
ต่อในเครื่องได้เลย แต่ถ้ายังมี material uncertainty ให้ถาม ChatGPT ก่อนเดาหรือ
ขยายการแก้ไข

1. **ก่อน implementation — ถ้าแผนเองยังไม่ชัดให้ consult ก่อน** หลัง local
   evidence pass หนึ่งรอบ หากยังมีอย่างน้อยสองแนวทางที่สมเหตุผลและมี trade-off
   ไม่ชัด, มี lifecycle/API contract ที่ไม่คุ้นเคยแต่มีผลต่อ correctness หรือ agent
   ยังอธิบายไม่ได้ว่าจะ falsify fix ที่เสนออย่างไร ให้เรียก LinkGPT ไม่ต้องเรียก
   เพียงเพื่อยืนยัน mechanical edit ที่ชัดเจนอยู่แล้ว
2. **กลางงาน — ถ้า evidence ใหม่ทำให้แผนเดิมอ่อนลงให้หยุดถามก่อนแก้ต่อ** เช่น
   targeted test สวนกับ hypothesis เดิม, fix แรกไม่หายและก้าวถัดไปต้องพึ่ง
   hypothesis ใหม่ที่ยังไม่พิสูจน์, scope ขยายไปยัง high-risk boundary อย่างไม่
   คาดคิด, ยังเหลือสองแนวทางที่สมเหตุผลหลัง direct check หนึ่งครั้ง หรือ evidence
   ในเครื่องยังขัดกันเอง
3. **ก่อนปิดงาน — ใช้ closure audit เมื่อผู้ใช้สั่ง audit workflow หรือเมื่องาน
   ต้องการ final audit** ให้ ChatGPT พยายาม falsify ผลที่แก้แล้ว หา residual risk
   และระบุ validation ที่ยังขาดให้น้อยที่สุด การ consult ก่อนหน้านี้ไม่แทน closure
   check ถ้า final audit เป็นส่วนหนึ่งของ workflow ที่ผู้ใช้ร้องขอ

ห้าม chain advisor calls โดยไม่มี local work คั่นกลาง ทุก follow-up ต้องมี evidence
ใหม่, updated code/diff หรือ unresolved question ที่ชัดเจน หาก advisor สองรอบติด
ใน issue เดิมยังไม่ลด uncertainty ให้หยุด loop แล้ว narrow/re-frame คำถามจาก
หลักฐานในเครื่อง หรือถามผู้ใช้แทนการ consult ซ้ำไปเรื่อย ๆ

#### การเลือก `new_chat`

ใช้ `new_chat=true` เมื่อ:

- เริ่ม independent review ใหม่;
- review หลักฐานคนละชุดหรือคนละเรื่อง;
- ต้องการ fresh opinion ที่ไม่ควรอิงคำตอบก่อนหน้า;
- ผู้ใช้ระบุคำว่า `independent`, `fresh audit`, `from scratch` หรือความหมายเทียบเท่า

ใช้ `new_chat=false` เมื่อ:

- ถาม follow-up จาก review ก่อนหน้าทันที;
- ตรวจว่าการแก้ previous finding ปิดปัญหาแล้วหรือยัง;
- ส่ง code/diff เวอร์ชันใหม่ของ issue เดิมให้ตรวจซ้ำ;
- ขอให้ ChatGPT reconsider, clarify หรือ re-audit finding เดิม;
- prompt อ้างถึง `your previous finding`, `the previous audit`, `fixed X`, `re-audit this fix` หรือความหมายเทียบเท่า

สำหรับ fix-and-recheck loop ปกติ ให้คง conversation เดิม:

1. Initial audit: เรียก `chatgpt_ask(..., new_chat=true)`
2. ChatGPT รายงาน finding X
3. แก้ X ในเครื่อง
4. ตรวจซ้ำด้วย `chatgpt_ask(prompt="<finding X was fixed + actual updated code/diff inline>", new_chat=false)`
5. ทำข้อ 2-4 ซ้ำจนปิด findings จาก review ชุดนั้น
6. หลังปิด findings หมดแล้ว ใช้ `new_chat=true` อีกครั้งเฉพาะเมื่อผู้ใช้สั่ง หรือเมื่องานต้องการ final independent audit

แม้ใช้ `new_chat=false` ก็ต้องแนบ actual updated code หรือ diff ล่าสุด inline เสมอ
conversation history มี reasoning เดิม แต่ไม่ได้มีโค้ดเวอร์ชันใหม่ ให้ reuse
conversation เดิมสำหรับ mid-work question, fix-and-recheck และ closure audit ที่ยัง
เป็น issue/review thread เดิม ห้ามขอให้ ChatGPT ไปอ่าน local file เองหรืออนุมาน
การเปลี่ยนแปลงจาก file path เท่านั้น

หากหมดเวลาแต่ response ยังทำงานอยู่ ให้เรียก `chatgpt_last_response()` แทนการส่ง
prompt ซ้ำ สถานะที่คืนมาจะบอกว่า response ยังทำงานอยู่หรือเสร็จแล้ว

#### สัญญาการ review

ทุก request ที่อาจมีผลต่อการแก้โค้ดต้องแนบสัญญานี้ (คงข้อความภาษาอังกฤษไว้เพื่อ
ให้ ChatGPT ตีความได้ตรงกัน):

```text
Act as a read-only code reviewer and advisor. Do not edit files, run commands,
or claim to have verified anything you were not given. Identify only concrete,
actionable findings. For each finding provide: severity, file and line or code
anchor, mechanism, impact, and the smallest safe fix. Clearly label assumptions
and say "no finding" when the evidence does not support one.
```

ถ้า ChatGPT มี local connector แยก สัญญา read-only นี้ยังใช้เหมือนเดิม และไม่ควร
สมมติว่า connector นั้นพร้อมใช้งาน ให้ใส่โค้ดหรือ command output ที่สำคัญลงใน
prompt เพื่อให้ review ทำซ้ำได้

ตัวอย่างคำขอสำหรับ code review, การตัดสินใจด้าน design และ adversarial sanity
check อยู่ในส่วนภาษาอังกฤษด้านบน โดยควรใช้ contract เดิมและส่งเฉพาะบริบทที่
จำเป็น

#### การเรียกเครื่องมือ

ตรวจสอบความพร้อมหนึ่งครั้งก่อน review โดย bridge เป็นเจ้าของ readiness budget
30 วินาทีตั้งแต่ก่อนเปิด browser และครอบคลุม navigation กับ page loading ผู้เรียก
จึงไม่ต้อง poll สถานะ `loading` เอง ให้ทำต่อเฉพาะเมื่อเป็น `ready` และหยุดให้ผู้ใช้
จัดการเมื่อเป็น `login_required` หรือ `challenge` หาก readiness timeout ก่อนส่ง
prompt bridge จะปิด browser ก่อนคืนผล และจะรายงาน ownership ที่ไม่แน่นอนหากปิด
ไม่สำเร็จ:

```text
chatgpt_status()
```

จากนั้นส่ง request ที่มีขอบเขตชัดเจน โดย timeout เริ่มต้นคือ 600 วินาที:

```text
chatgpt_ask(
  prompt="<review contract + focused context>",
  new_chat=true,
  timeout_seconds=600
)
```

ระหว่างรอ MCP client ที่รองรับ progress จะได้รับสถานะว่า prompt ถูกส่งแล้ว กำลัง
รอ กำลังสร้างคำตอบ หรือกำลังยืนยันความเสถียร ระหว่างสร้างคำตอบจะมี tail ล่าสุด
ของข้อความที่มองเห็นได้ (ไม่เกิน 1,200 ตัวอักษร) เพื่อให้ agent เข้าใจบริบท
ปัจจุบันก่อนคำตอบสุดท้ายมา ห้ามเรียก browser tool อื่นจนกว่า `chatgpt_ask` จะคืน
ผลสำเร็จ ซึ่งจะมี `status: "completed"` และข้อความยืนยันว่าคำตอบสุดท้ายพร้อมใช้
ให้คง dedicated browser เปิดไว้ตราบใดที่ issue/review thread เดิมยังทำงานอยู่ เพื่อ
ให้ follow-up ด้วย `new_chat=false` ใช้ conversation เดิมได้ แล้วค่อยปิดหลัง
closure audit, recovery budget หมด หรือ abandon review thread
หากมี `correlation_status: "rendering_fallback"` ให้เก็บและแจ้ง
`correlation_warning` เมื่อความแตกต่างมีผลต่อการตัดสินใจ และไม่ควรกล่าวว่าเป็น
คำตอบที่ correlate กับ rendered text แบบ exact

ถ้า `chatgpt_ask` timeout ห้ามส่ง prompt ซ้ำ ให้เว้น 2-5 วินาทีระหว่างการเรียก
`chatgpt_last_response()` แบบมีขอบเขต และยอมรับเฉพาะ `status: "completed"`
สำหรับ error หลังส่ง prompt แบบอื่น โดยเฉพาะ user-turn, conversation, target หรือ
page-change ให้ถือว่า ownership ของ response ไม่ชัดเจน ทิ้ง partial tail ห้ามใช้
`chatgpt_last_response()` เป็นคำแนะนำ ห้าม retry หรือเปิด chat ใหม่อัตโนมัติ และ
รายงานว่า review ไม่สำเร็จ การเริ่มใหม่ต้องได้รับคำสั่งจากผู้ใช้อย่างชัดเจน

ปิด browser เฉพาะหลัง review เสร็จ:

```text
chatgpt_close()
```

ใช้ 900 วินาทีเฉพาะ review ที่กว้างเป็นพิเศษ สำหรับคำถามทั่วไปให้ใช้ค่าเริ่มต้น
หาก timeout แต่ review ยังทำงาน ให้รอสักครู่แล้วเรียก
`chatgpt_last_response()` timeout ไม่ได้แปลว่า review ล้มเหลว

### การใช้งานและโควตา

bridge นี้ไม่รวมบัญชี API key หรือ context ของ ChatGPT กับ Codex ในการใช้งานปกติ
คำตอบ reviewer ถูกสร้างในบริบทของ ChatGPT Web และอยู่ภายใต้ข้อจำกัดของบริการ
นั้น ขณะเดียวกัน Codex ยังใช้ context และ usage ของตัวเองในการสร้าง MCP call และ
ประมวลผลผลลัพธ์ เส้นทางนี้จึงไม่ใช่ช่องทางฟรีหรือไม่จำกัด

- ChatGPT Web ปกติและงาน Codex เป็น usage surface แยกกันใน workflow นี้ และอยู่
  ภายใต้ account/plan เดียวกันตามเงื่อนไขของบริการ
- ChatGPT Work และ Codex ใช้ usage, credits และ limits ร่วมกัน อย่าสมมติว่าแยก
  กันหาก browser conversation ใช้ Work หรือ agentic feature อื่น
- token ใน prompt, code excerpt, tool result และ response ยังคงนับใน context ที่
  ประมวลผล ควรส่งบริบทให้กระชับและกำหนดขอบเขต review

เอกสาร pricing อย่างเป็นทางการอยู่ที่
<https://learn.chatgpt.com/docs/pricing>

### ความต้องการของระบบ

- macOS ที่ติดตั้ง Google Chrome
- Python 3.11
- บัญชี ChatGPT ที่ลงชื่อเข้าใช้ได้ผ่าน browser window ที่มองเห็นได้

environment ที่ผ่านการตรวจสอบ:

```text
/opt/homebrew/anaconda3/envs/mlx/bin/python3
browser-use 0.12.9
mcp 1.26.0
```

ติดตั้ง dependency ใน Python 3.11 เมื่อจำเป็น:

```bash
python3.11 -m pip install -r requirements.txt
```

### การใช้งานครั้งแรก

1. เริ่ม MCP server หรือเรียก `chatgpt_status` จาก Codex
2. จะมี Chrome window เฉพาะเปิดขึ้นมา โดยใช้
   `~/.codex-chatgpt/browser-profile`
3. ลงชื่อเข้าใช้ ChatGPT ด้วยตนเอง ห้ามส่ง credentials ให้ Codex หรือ tool
4. หลังลงชื่อเข้าใช้แล้ว เรียก `chatgpt_status` อีกหนึ่งครั้งและรอผลสุดท้าย
   ซึ่งควรเป็น `ready`

เปลี่ยนตำแหน่ง profile ได้เมื่อจำเป็น:

```bash
export CODEX_CHATGPT_PROFILE_DIR="$HOME/.codex-chatgpt/browser-profile"
```

เก็บ profile ไว้นอก repository เสมอ เพราะมี browser state ที่ยืนยันตัวตนแล้ว
และห้าม commit หรือแชร์

browser จะแสดงให้เห็นเพื่อให้ผู้ใช้ login หรือจัดการ human-verification แต่
ค่าเริ่มต้นเป็นหน้าต่างขนาดเล็ก (`760x560` ที่ตำแหน่ง `24,60`) แทนการ maximize
ปรับได้โดยไม่ต้องแก้โค้ด:

```bash
export CODEX_CHATGPT_WINDOW_WIDTH=900
export CODEX_CHATGPT_WINDOW_HEIGHT=700
export CODEX_CHATGPT_WINDOW_X=100
export CODEX_CHATGPT_WINDOW_Y=120
```

ค่าตำแหน่งใช้จุดกำเนิดมุมซ้ายบน และส่ง size/position ให้ native headed Chrome
ของ browser-use หลังเปลี่ยนตัวแปรต้อง restart MCP process

profile path เริ่มต้นและ prefix `CODEX_CHATGPT_*` เป็น runtime identifier แบบ
legacy ที่คงไว้เพื่อให้ profile และ local setup เดิมใช้งานต่อได้หลังการ rename
โปรเจกต์

### การทดสอบ

รัน deterministic unit tests และ MCP initialization/list-tools handshake โดยไม่
เปิด Chrome:

```bash
/opt/homebrew/anaconda3/envs/mlx/bin/python3 -m unittest discover -s tests -v
```

หลัง login แล้วอาจทำ live test แบบตั้งใจ เช่น ขอให้ ChatGPT ตอบ `PONG` เท่านั้น
การเปลี่ยนแปลง UI ของ chatgpt.com อาจทำให้ selector เสีย แม้ unit และ protocol
tests จะยังผ่าน

bridge ถือว่า `auth.openai.com` เป็น login flow จาก URL และแยก signed-out controls
ออกจาก conversation content ทั้งนี้ยังขึ้นกับ DOM และ completion signals ที่
เปลี่ยนแปลงได้ของ ChatGPT Web การยกเลิกหลังส่ง prompt แล้วไม่ใช่ rollback และ
ChatGPT อาจประมวลผล request ต่อ

### การลงทะเบียนกับ Codex

ลงทะเบียน stdio server โดยแทน path ให้ตรงกับตำแหน่งที่ clone โปรเจกต์:

```bash
codex mcp add mcp-linkgpt -- \
  /opt/homebrew/anaconda3/envs/mlx/bin/python3 \
  /absolute/path/to/MCP-LinkGPT/server.py
```

Codex มี timeout ของ Tool แยกจาก MCP-LinkGPT เนื่องจาก bridge รองรับการ review
ที่ใช้เวลา 600-900 วินาที ให้เพิ่มบรรทัดนี้ในตาราง
`[mcp_servers.mcp-linkgpt]` ที่สร้างใน `~/.codex/config.toml`:

```toml
tool_timeout_sec = 900.0
```

timeout ฝั่ง host ต้องไม่น้อยกว่าค่า `chatgpt_ask(timeout_seconds=...)` สูงสุดที่
จะใช้ และต้อง restart Codex หลังแก้ค่า มิฉะนั้น host อาจตัด MCP request ขณะที่
bridge ยังถือ browser อยู่และรอ ChatGPT ทำงานให้เสร็จ

ตรวจสอบหรือลบการลงทะเบียน:

```bash
codex mcp get mcp-linkgpt
codex mcp remove mcp-linkgpt
```

หากรายการ tool ยังไม่ refresh ใน session ปัจจุบัน ให้ restart Codex หลังลงทะเบียน

[กลับไปด้านบน / Back to top](#mcp-linkgpt)
