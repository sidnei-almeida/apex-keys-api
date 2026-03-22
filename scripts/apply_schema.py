#!/usr/bin/env python3
"""
Aplica tabelas em falta com Base.metadata.create_all (igual ao init_db da API).

Não apaga dados nem remove colunas. Para base totalmente vazia com schema limpo, usa:
  python scripts/reset_and_apply_schema.py

Requer DATABASE_URL no .env.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.dotenv_loader import load_dotenv


async def _main() -> None:
    load_dotenv()
    from app.database import close_db, init_db

    await init_db()
    await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
    print("Esquema aplicado (create_all).")
