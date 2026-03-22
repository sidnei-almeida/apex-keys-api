#!/usr/bin/env python3
"""Aplica Base.metadata.create_all (mesmo esquema do arranque da API)."""

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
