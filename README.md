# Objection!

> A council of LLMs that answer independently, object to each other, verify claims with tools — and return one answer with honest disagreement.

**Status:** design stage. No code yet — see [docs/](docs/).

## Why

Asking several models and letting them "debate until they agree" sounds great, but research shows naive debate mostly reproduces majority voting, and models often abandon correct answers under peer pressure (sycophancy). Objection! is built around what actually works:

- **Independent first answers** — no model sees the others before committing.
- **Task-aware protocols** — `verify`, `code`, `review`, `deliberate`, `quick` instead of one generic debate.
- **Debate only on disagreement** — at most 1–2 anonymous, anti-conformist critique rounds.
- **Verification over rhetoric** — claims are checked with web search, a Python sandbox, and tests.
- **Measured quality** — every preset is benchmarked against the best single model and self-consistency at the same budget.
- **Your models, your pool** — any model via [LiteLLM](https://github.com/BerriAI/litellm) (cloud or local: Ollama, vLLM, LM Studio). The pool is defined by you and can change at any time.
- **Honest output** — confidence, agreement, disputed points and a minority report, not a fake consensus.

## Planned interfaces

- Python library
- CLI with JSON / Markdown output and CI-friendly exit codes
- MCP server for agentic coding tools — first targets: **OpenCode** and **Cline**
- Web UI from day one: runs dashboard, live run trace, debate and report views; light and dark themes
- Later: HTTP API and multi-user mode

## Documentation (Russian)

- [Design](docs/design.md)
- [Research summary](docs/research.md)
- [Decisions log](docs/decisions.md)
- [Agent integrations (OpenCode, Cline)](docs/integrations.md)
- [Roadmap](docs/roadmap.md)
- [Example config](examples/objection.example.yaml)

## License

[MIT](LICENSE)
