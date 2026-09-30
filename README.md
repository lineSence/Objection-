# Objection!

> A council of LLMs that answer independently, object to each other, verify claims with tools — and return one answer with honest disagreement.

**Status:** M3 done, M4 in progress. Works today: five presets with an `auto` router — `deliberate` (independent answers → anonymous sparse critique → synthesis), `review` (independent reviews → dedupe → cross-check votes → `pass`/`fail`/`uncertain`), `verify` (weighted vote, early stop), `quick` (two models, escalate on disagreement), `code` (candidates judged by your tests); the Verifier (claims checked with SearXNG web search, a Python sandbox and your repository files — evidence outranks votes, also for review findings); health checks before every run, retries and fallbacks; per-model outcome statistics and answer labels; the **Eval Harness** (`objection eval run`: presets vs best single model, self-consistency and plain voting at equal budget); an MCP server for OpenCode / Cline; result cache; CLI; run history in SQLite; the Web UI (overview, chat, debate, trace, report; light/dark themes). Design docs: [docs/](docs/).

## Why

Asking several models and letting them "debate until they agree" sounds great, but research shows naive debate mostly reproduces majority voting, and models often abandon correct answers under peer pressure (sycophancy). Objection! is built around what actually works:

- **Independent first answers** — no model sees the others before committing.
- **Task-aware protocols** — `verify`, `code`, `review`, `deliberate`, `quick` instead of one generic debate.
- **Debate only on disagreement** — at most 1–2 anonymous, anti-conformist critique rounds.
- **Verification over rhetoric** — claims are checked with web search, a Python sandbox, and tests.
- **Measured quality** — every preset is benchmarked against the best single model and self-consistency at the same budget.
- **Your models, your pool** — any model via [LiteLLM](https://github.com/BerriAI/litellm) (cloud or local: Ollama, vLLM, LM Studio). The pool is defined by you and can change at any time.
- **Honest output** — confidence, agreement, disputed points and a minority report, not a fake consensus.

## Quick start

```bash
git clone https://github.com/lineSence/Objection-.git objection && cd objection
bash start.sh          # Linux/macOS: creates .venv, installs, opens the Web UI at http://127.0.0.1:6967
start.bat              # Windows (or double-click it)
bash start.sh ask "SQLite or PostgreSQL for run history?"
git diff | bash start.sh review -    # council review of your changes
```

Or install the `objection` command globally (handy for MCP clients): `pipx install git+https://github.com/lineSence/Objection-.git`, then `objection ui`. Details (in Russian): [docs/install.md](docs/install.md).

Without a config Objection! uses offline `mock/*` models, so you can try the UI right away. Add real models in the Web UI (**Settings**: any LiteLLM provider, Ollama / LM Studio / LiteLLM Proxy / OpenAI-compatible servers, API keys, connection test) or define your pool in `~/.objection/config.yaml` (or `./objection.yaml`, or `$OBJECTION_CONFIG`) — see [the example](examples/objection.example.yaml). API keys are read by LiteLLM from the usual environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …).

| Command | What it does |
| --- | --- |
| `objection ask "…" [--mode auto\|deliberate\|verify\|quick] [--check/--no-check] [--json] [-m a,b,c] [--budget 0.2]` | Ask the council; `auto` (default) routes to the cheapest fitting protocol; claims in the answer are fact-checked via your SearXNG + python sandbox ([docs/verifier.md](docs/verifier.md)); exit code 1 on failure |
| `objection verify "claim"` | Fact-check a claim by weighted vote; exit 0 confirmed, 1 refuted, 2 unverified, 3 error |
| `objection solve "task" --tests "pytest -q" [--workdir .] [--file path] [--apply]` | Every model writes a solution, your tests pick the winner (tests run in a best-effort sandbox: temp copy, scrubbed env, rlimits, no network on Linux — not a security boundary); exit 0 pass, 1 fail |
| `objection review [FILE\|-] [--diff REF] [--staged] [--kind diff\|plan\|file\|text] [--fail-on high] [--check] [--workdir .] [--format json]` | Council code/plan review; `--check` also verifies every finding against the repository files / Python / web (a refuted finding is rejected); exit 0 pass, 1 fail, 2 uncertain, 3 error |
| `objection eval run [--suite math-mini\|code-mini\|mine\|file.jsonl] [--presets verify,quick] [--n 20]` | Measure presets against the best single model, self-consistency and plain voting **at equal budget** on your pool; `eval list` / `eval show <id>` ([docs/eval.md](docs/eval.md)); exit 0 every preset wins, 1 some preset does not |
| `objection mcp` | MCP server over stdio (tools `council_ask`, `council_review`, `council_verify`, `council_solve`, `council_models`) — see [docs/integrations.md](docs/integrations.md) |
| `objection ui [--port 6967]` | Local Web UI (binds to 127.0.0.1) |
| `objection models list` / `check` / `add <id> <litellm-model> [--fallback …]` / `remove <id>` | Show / health-check / edit the pool |
| `objection runs list` / `show <id>` / `delete <id…>` / `label <id> correct\|wrong [--expected X]` / `reindex` | Run history; labels build your own eval set (`--suite mine`) |
| `objection sandbox` | Show what the local sandbox can isolate on this machine ([docs/sandbox.md](docs/sandbox.md)) |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
cd web && npm install && npm run dev     # Vite dev server, proxies /api to :6967
npm run build                            # writes src/objection/web_dist (bundled into the wheel)
```

Backend: FastAPI inside the Python package (`src/objection`). Frontend: React + TypeScript + Vite (`web/`). The built UI is committed to `src/objection/web_dist`, so installing from git needs no Node.

## Interfaces

- Python library
- CLI with JSON / Markdown output and CI-friendly exit codes
- MCP server for agentic coding tools — **OpenCode** and **Cline** ([setup](docs/integrations.md))
- Web UI from day one: runs dashboard, live run trace, debate and report views; light and dark themes
- Later: HTTP API and multi-user mode

## Documentation (Russian)

- [Design](docs/design.md)
- [Research summary](docs/research.md)
- [Decisions log](docs/decisions.md)
- [Installation (Russian)](docs/install.md)
- [Agent integrations (OpenCode, Cline)](docs/integrations.md)
- [Eval Harness](docs/eval.md)
- [Roadmap](docs/roadmap.md)
- [Example config](examples/objection.example.yaml)

## License

[MIT](LICENSE)
