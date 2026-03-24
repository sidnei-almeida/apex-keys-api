#!/usr/bin/env python3
"""
Aplica a migration add_featured_tier ao Postgres.
Necessário quando a tabela raffles já existia antes de featured_tier ser adicionado ao modelo.

Uso:
  python scripts/apply_featured_tier_migration.py

Requer DATABASE_URL (ou DATABASE_PRIVATE_URL no Railway) no ambiente ou .env.
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
    from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    engine = create_async_engine(url, connect_args=connect_args)

    sql = """
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'raffles' AND column_name = 'featured_tier'
      ) THEN
        ALTER TABLE raffles ADD COLUMN featured_tier VARCHAR(20) DEFAULT 'none';
        UPDATE raffles SET featured_tier = 'none' WHERE featured_tier IS NULL;
        RAISE NOTICE 'Coluna featured_tier adicionada.';
      ELSE
        RAISE NOTICE 'Coluna featured_tier já existe.';
      END IF;
    END $$;
    """

    async with engine.begin() as conn:
        await conn.execute(text(sql))

    await engine.dispose()
    print("Migration featured_tier aplicada com sucesso.")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
