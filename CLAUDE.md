# MCP-LinkGPT project rules

- This project is an MCP bridge from Codex to ChatGPT Web through `browser-use`.
- Keep it independent from `ENDEAVOR_AGENT_CHATGPT`; do not import or edit that project.
- Do not add an autonomous browser agent or a second LLM. Browser actions must remain deterministic.
- Never read, print, export, or commit browser cookies, passwords, prompts, or responses.
- Keep the browser profile outside the repository by default.
- Restrict automatic navigation to approved ChatGPT/OpenAI domains and stop on CAPTCHA or login gates.
- Run `python3 -m unittest discover -s tests -v` after code changes.
