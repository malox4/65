#!/usr/bin/env bash
# Запуск графика на своём ПК (macOS / Linux).
set -euo pipefail
cd "$(dirname "$0")"
echo "График: http://127.0.0.1:8765"
echo "Оставьте это окно открытым. Остановка: Ctrl+C"
exec python3 -m bot.server
