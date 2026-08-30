# MCP-LinkGPT project rules

- This public repository is self-contained. Do not assume access to a private parent repository or external agent rules.
- Read [`AGENT.md`](AGENT.md) for the quick repository-agent overview and [`AGENT_PROCEDURE.md`](AGENT_PROCEDURE.md) for the full operating procedure before substantial work.
- This project is an MCP bridge from Codex to ChatGPT Web through `browser-use`.
- Keep it independent from `Endeavor_Hands`; do not import or edit that project.
- Do not add an autonomous browser agent or a second LLM. Browser actions must remain deterministic.
- Never read or export browser cookies/passwords/storage through browser APIs. Prompts and responses may be processed transiently only for the intended MCP request/response and correlation flow; never persist them to application logs, commit them, or expose them to unrelated destinations.
- Keep the browser profile outside the repository by default.
- Restrict automatic navigation to approved ChatGPT/OpenAI domains and stop on CAPTCHA or login gates.
- Run `python3 -m unittest discover -s tests -v` after code changes.
