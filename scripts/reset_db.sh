#!/usr/bin/env bash
# Zera o schema public e reaplica schema.sql (usa Python + asyncpg; não precisa de psql).
# Uso: ./scripts/reset_db.sh   (na raiz do repo; lê .env se existir)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/reset_db.py"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/scripts/reset_db.py"
fi
echo "Erro: precisa de Python 3 com asyncpg (ex.: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)." >&2
exit 1
