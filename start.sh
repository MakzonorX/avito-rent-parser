#!/usr/bin/env bash
# Запуск веб-админки парсера аренды: ./start.sh  →  http://localhost:9999
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Создаю окружение..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m playwright install chromium
fi

echo "Веб-интерфейс: http://localhost:9999"
exec .venv/bin/python run_web.py "$@"
