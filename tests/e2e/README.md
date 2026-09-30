# e2e: OpenCode и Cline со скриптовой моделью

Проверяет подключение MCP-сервера в настоящих клиентах без платных ключей. `fake_llm.py` — локальный сервер с
OpenAI-совместимым (`/v1/*`) и Ollama (`/api/*`) API; ответы совета берутся из mock-ролей Objection!, а модель
`fake-agent` ведёт себя как агент: сначала вызывает инструмент `…council_review` с тестовым diff, затем пересказывает вердикт.

```bash
.venv/bin/python -m uvicorn --app-dir tests/e2e fake_llm:app --port 9111   # ключ: sk-fake-1234567890
```

**OpenCode** (`opencode.json` в пустой папке проекта):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "fake": {
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "http://127.0.0.1:9111/v1", "apiKey": "sk-fake-1234567890" },
      "models": { "fake-agent": {} }
    }
  },
  "model": "fake/fake-agent",
  "mcp": { "objection": { "type": "local", "command": ["/abs/path/.venv/bin/objection", "mcp"], "enabled": true } }
}
```

```bash
opencode mcp list                       # ✓ objection connected
opencode run "Проверь изменения советом"  # агент вызывает objection_council_review
```

**Cline CLI**:

```bash
cline auth -p openai -k sk-fake-1234567890 -m fake-agent -b http://127.0.0.1:9111/v1
cline mcp add objection --yes -- /abs/path/.venv/bin/objection mcp
cline -c /path/to/project "Проверь изменения советом"   # агент вызывает objection__council_review
```

Запуски появятся в `objection ui` с источником `opencode` / `cline`. Проверено: OpenCode 1.18.33, Cline CLI 3.0.66.
