#!/usr/bin/env bash
# Objection! — быстрый запуск (Linux / macOS).
#   bash start.sh              → Web UI (http://127.0.0.1:8765)
#   bash start.sh ask "…"      → любая команда objection
# При первом запуске создаёт .venv и ставит пакет; повторно — только если изменился pyproject.toml.
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for c in python3.13 python3.12 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  PY=$(find_python) || { echo "Нужен Python 3.12+ — https://www.python.org/downloads/" >&2; exit 1; }
  echo "→ Создаю окружение .venv ($("$PY" --version))"
  "$PY" -m venv .venv
fi

stamp=$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("pyproject.toml","rb").read()).hexdigest())')
if [ ! -x .venv/bin/objection ] || [ "$(cat .venv/.objection-installed 2>/dev/null)" != "$stamp" ]; then
  echo "→ Устанавливаю зависимости (1–2 минуты при первом запуске)…"
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -e .
  echo "$stamp" > .venv/.objection-installed
fi

if [ -z "${OBJECTION_CONFIG:-}" ] && [ ! -f objection.yaml ] && [ ! -f "${OBJECTION_HOME:-$HOME/.objection}/config.yaml" ]; then
  echo "ℹ Конфиг не найден — работают офлайн mock-модели. Свой пул моделей:"
  echo "  mkdir -p ~/.objection && cp examples/objection.example.yaml ~/.objection/config.yaml"
fi

[ $# -eq 0 ] && set -- ui
exec .venv/bin/objection "$@"
