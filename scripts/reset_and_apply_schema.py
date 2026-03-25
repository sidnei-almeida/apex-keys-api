#!/usr/bin/env python3
"""
Limpa a base de dados e recria o schema actual (app/models.py).

Executa Base.metadata.drop_all + create_all sobre:
  users, raffles, tickets, transactions

⚠️  APAGA TODOS OS DADOS.

Uso:
  python scripts/reset_and_apply_schema.py

Depois, para o primeiro administrador (sem API pública de promoção):
  1. Define ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_WHATSAPP (env ou .env)
  2. python scripts/create_admin.py

Para rifas de desenvolvimento:
  3. python scripts/seed_bulk_raffles.py

Equivalente a aplicar `schema.sql` linha a linha: python scripts/reset_db.py

Requer DATABASE_URL no .env (ou ambiente). Mesma lógica SSL que app/database.py.
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
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
    from app.models import Base

    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    engine = create_async_engine(url, echo=False, connect_args=connect_args)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
    print("DB limpo e esquema reaplicado (models actuais).")
    print("Próximo passo: define ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_WHATSAPP e corre: python scripts/create_admin.py")
