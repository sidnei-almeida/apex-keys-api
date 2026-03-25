"""
Reset total do schema (DEV/TESTE).

- Lê DATABASE_URL/DATABASE_PRIVATE_URL/POSTGRES_URL do ambiente (mesma lógica da API)
- Faz DROP ALL TABLES (via SQLAlchemy metadata) e recria tudo

ATENÇÃO: isto apaga TODOS os dados do banco.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
from app.models import Base


async def main() -> None:
    resolved = _resolve_database_url()
    url, connect_args = _url_without_sslmode_for_asyncpg(resolved)
    engine = create_async_engine(url, echo=False, connect_args=connect_args)
    try:
        async with engine.begin() as conn:
            # Reset "hard" do schema para evitar erros de dependência (FKs).
            # ATENÇÃO: apaga tudo no schema public.
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            # Permissões padrão (compatível com Railway / Postgres gerenciado)
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    print("OK: schema resetado (DROP SCHEMA public CASCADE + create_all).")


if __name__ == "__main__":
    asyncio.run(main())

