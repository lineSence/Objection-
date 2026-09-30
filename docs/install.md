# Установка и запуск

Нужен **Python 3.12+** ([python.org](https://www.python.org/downloads/); на Windows при установке отметьте «Add python.exe to PATH»). Node.js не нужен — собранный Web UI уже внутри пакета.

## Вариант 1. Скрипт быстрого запуска (из исходников)

Скрипт сам создаёт `.venv`, ставит зависимости (только при первом запуске или после изменения `pyproject.toml`) и открывает Web UI на http://127.0.0.1:6967. Любые аргументы передаются в `objection`.

**Linux / macOS**

```bash
git clone https://github.com/lineSence/Objection-.git objection
cd objection
bash start.sh                      # Web UI
bash start.sh ask "SQLite или Postgres?"
git diff | bash start.sh review -  # ревью изменений
```

**Windows** — двойной клик по `start.bat` или в PowerShell:

```powershell
git clone https://github.com/lineSence/Objection-.git objection
cd objection
.\start.bat                        # Web UI
.\start.bat ask "SQLite или Postgres?"
```

Без git: на GitHub нажмите **Code → Download ZIP**, распакуйте и запустите `start.bat` / `bash start.sh`.

Обновление: `git pull` и снова скрипт — зависимости доустановятся сами.

## Вариант 2. Установка командой (без исходников)

Команда `objection` станет доступна глобально — это удобно для MCP в OpenCode / Cline.

```bash
pipx install git+https://github.com/lineSence/Objection-.git
# или: uv tool install git+https://github.com/lineSence/Objection-.git
objection ui
```

Обновление: `pipx upgrade objection` (или `uv tool upgrade objection`).

Без pipx/uv — в своё виртуальное окружение:

```bash
python -m venv ~/.objection-venv
~/.objection-venv/bin/pip install git+https://github.com/lineSence/Objection-.git      # Windows: %USERPROFILE%\.objection-venv\Scripts\pip
~/.objection-venv/bin/objection ui
```

## Свои модели

Проще всего — в Web UI: **Настройки** (http://127.0.0.1:6967/#/settings).

1. «+ Добавить модель» → выберите провайдера (OpenAI, Anthropic, Gemini, OpenRouter, DeepSeek, Mistral, Groq, xAI, Ollama, LM Studio, LiteLLM Proxy или свой OpenAI-совместимый URL).
2. Введите ключ (для облачных) или адрес сервера и нажмите «Найти модели» (для локальных и прокси). Для облачных подсказки берутся из каталога LiteLLM вместе с ценой.
3. «Проверить» — модель должна ответить «ok». «Сохранить».
4. Уберите mock-модели и при желании закрепите состав совета в «Совет по умолчанию».

Конфиг пишется в `~/.objection/config.yaml`, ключи — отдельно в `~/.objection/secrets.env` (права только для вашего пользователя). Web UI применяет изменения сразу, CLI и MCP — при следующем запуске.

Или вручную:

Без конфига работают офлайн mock-модели — можно сразу посмотреть интерфейс. Чтобы подключить настоящие:

```bash
mkdir -p ~/.objection && cp examples/objection.example.yaml ~/.objection/config.yaml   # Windows: mkdir ~\.objection; copy examples\objection.example.yaml ~\.objection\config.yaml
export OPENAI_API_KEY=… ANTHROPIC_API_KEY=…                                             # Windows: $env:OPENAI_API_KEY="…"
objection models check
```

Конфиг ищется так: `$OBJECTION_CONFIG` → `./objection.yaml` → `~/.objection/config.yaml`. Подключение к агентам — [integrations.md](integrations.md).
