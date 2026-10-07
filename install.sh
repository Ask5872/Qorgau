#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/check_setup.py --load-models
echo 'Запуск демонстрации: .venv/bin/python run.py --demo'
echo 'Запуск камеры: .venv/bin/python run.py'
