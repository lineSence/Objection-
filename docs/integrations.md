# Интеграция с агентами разработки

Objection! подключается к агентам как **MCP-сервер** (транспорт stdio): клиент сам запускает `objection mcp` и общается с ним через stdin/stdout. Первые целевые клиенты — **OpenCode** и **Cline**.

> **Статус проверки (M2).** Живой прогон сделан 2026-09-30 в **OpenCode 1.18.33** (`opencode run`) и **Cline CLI 3.0.66** (`cline -c <проект> "…"`): агент сам вызвал `council_review`, получил вердикт, запуск появился в Web UI с источником `opencode` / `cline`. Агентом была скриптовая фейковая модель через OpenAI-совместимый сервер (`tests/e2e/`), то есть проверено подключение и протокол, а не качество работы реальных моделей. Расширение Cline для VS Code отдельно не проверялось (формат настроек тот же). Все пять инструментов покрыты e2e-тестом через MCP SDK-клиент (`tests/test_mcp.py`).

Все запуски из агентов пишутся в тот же Run Store, что и Web UI: откройте `objection ui` и смотрите их в «Обзоре» (источник — `OpenCode` / `Cline`, берётся из `clientInfo` MCP-клиента). Каждый ответ инструмента содержит ссылку `ui` на запуск.

## Инструменты

| Инструмент | Вход | Выход | Когда агенту вызывать |
| --- | --- | --- | --- |
| `council_ask` | `question`, `context?`, `mode?` (`auto` по умолч. / `deliberate` / `verify` / `quick`), `models?`, `budget_usd?`, `no_cache?` | `answer`, `confidence`, `agreement`, `disputed`, `minority_report`; для `verify`/`quick` — `votes`, `stopped_early`, `escalated_to`; `routed` — куда отправил авто-режим | архитектурное решение, выбор подхода, «второе мнение», короткий фактический вопрос |
| `council_review` | `target` (дифф / план / файл / текст), `kind`, `instructions?`, `context?`, `fail_on?` (по умолч. `high`), `models?`, `budget_usd?`, `no_cache?` | `verdict` (`pass` / `fail` / `uncertain`), `findings[]` с `severity`, `status`, голосами | перед коммитом / PR, перед реализацией плана |
| `council_models` | `check?` | пул моделей и (если `check`) их доступность | диагностика |
| `council_verify` | `claim`, `context?`, `models?`, `budget_usd?`, `no_cache?` | `status` (`confirmed` / `refuted` / `unverified`), `votes`, `agreement`, `minority_report`, `stopped_early` | проверить факт перед тем, как на него опереться: поведение API, версия, число |
| `council_solve` | `task`, `tests_cmd?`, `workdir?`, `solution_path?`, `context?`, `models?`, `budget_usd?` | `verdict` (`pass` / `fail` / `uncertain`), `solution` {`filename`, `code`, `model_id`, `passed`}, `candidates[]` | несколько независимых решений, победителя выбирают ваши тесты; агент сам применяет `solution.code` |

**Внимание (`council_solve`):** код моделей запускается локально, во временной копии `workdir` (исходная папка не меняется), с таймаутом `OBJECTION_TEST_TIMEOUT_S` (120 с), но **без песочницы** — песочница запланирована в M3.

Имена инструментов в клиентах получают префикс сервера: в OpenCode — `objection_council_review`, в Cline — `objection__council_review`.

Как считается `verdict` в `council_review`: находка **подтверждена**, если за неё ≥ 2 голосов и подтверждений больше, чем опровержений; **спорна**, если голоса разделились; **отклонена** — иначе. `fail` — есть подтверждённая находка с серьёзностью ≥ `fail_on`; `uncertain` — есть только спорные находки такого уровня или бюджет кончился посреди ревью; иначе `pass`. Вердикт считает код, а не модель.

## OpenCode

Файл `opencode.json` в корне проекта или глобально `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "objection": {
      "type": "local",
      "command": ["objection", "mcp"],
      "enabled": true,
      "environment": {
        "OBJECTION_CONFIG": "/home/me/.objection/config.yaml"
      }
    }
  }
}
```

Проверено в OpenCode 1.18.33 именно с этим форматом (`opencode mcp list` → `✓ objection connected`). `OBJECTION_CONFIG` можно не указывать, если конфиг лежит по умолчанию (`~/.objection/config.yaml`; `./objection.yaml` в рабочей папке клиента тоже подхватится). Ключи провайдеров (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, …) наследуются из окружения OpenCode или задаются в `environment`.

В документации будущей OpenCode v2 серверы вложены в `mcp.servers`, а таймауты задаются объектом; совет из нескольких моделей может думать дольше минуты, поэтому стоит поднять `execution`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "timeout": { "execution": 600000 },
    "servers": {
      "objection": { "type": "local", "command": ["objection", "mcp"] }
    }
  }
}
```

## Cline

`cline_mcp_settings.json` (в Cline: MCP Servers → Configure → Edit configuration):

```json
{
  "mcpServers": {
    "objection": {
      "command": "objection",
      "args": ["mcp"],
      "env": { "OBJECTION_CONFIG": "/home/me/.objection/config.yaml" },
      "timeout": 600,
      "disabled": false,
      "autoApprove": ["council_models"]
    }
  }
}
```

В Cline CLI то же самое делает команда `cline mcp add objection -- objection mcp` (она пишет вложенный формат `{"transport": {"type": "stdio", "command", "args"}}` в `~/.cline/data/settings/cline_mcp_settings.json`); плоский формат выше CLI тоже понимает — проверено в Cline CLI 3.0.66.

`timeout` в Cline — в секундах. Если `objection` установлен в virtualenv, укажите полный путь: `"command": "/path/to/.venv/bin/objection"`.

## Подсказка агенту (AGENTS.md / .clinerules)

```markdown
## Совет моделей (Objection!)
- Перед коммитом: вызови `council_review` с `kind: "diff"` и выводом `git diff`. При `verdict: "fail"` исправь подтверждённые находки и повтори; при `uncertain` — сообщи пользователю спорные пункты.
- Перед реализацией большого плана: `council_review` с `kind: "plan"`.
- Если выбираешь между подходами или застрял после двух красных прогонов тестов: `council_ask`.
- Не вызывай совет на тривиальные правки. Повторный вызов с тем же входом бесплатен (кэш).
```

## Правила для агентного режима

- **Чистый контекст ревьюеров:** модели совета видят только задачу, материал и переданный `context` — не историю диалога агента-автора.
- **Неинтерактивность:** вместо уточняющих вопросов — поле `assumptions`.
- **Жёсткие лимиты:** `budget_usd` на вызов; при превышении — частичный результат с `verdict: "uncertain"`.
- **Кэш:** хэш (вход + состав + режим + версия протокола) → повторный вызов в цикле бесплатен и помечается `cached: true`. Отключается `no_cache: true`.
- **Без рекурсии:** совет не вызывает совет.
- **Ветвление:** агент опирается на `verdict` и `findings[].severity`.

## CLI для CI и git-хуков

```bash
objection review --diff HEAD~1 --fail-on high          # дифф относительно ревизии
objection review --staged --format json                # то, что пойдёт в коммит
git diff main | objection review - --kind diff         # из stdin
objection review PLAN.md --kind plan
# exit 0 — pass, 1 — fail, 2 — uncertain, 3 — ошибка (конфиг, бюджет, нет моделей)
```
