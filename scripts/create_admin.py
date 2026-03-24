#!/usr/bin/env python3
"""
Cria ou actualiza um utilizador administrador (bcrypt igual à API).

Fluxo típico após base limpa:
  1. python scripts/reset_and_apply_schema.py
  2. Define variáveis de ambiente (recomendado) ou edita os defaults abaixo
  3. python scripts/create_admin.py

Variáveis de ambiente (têm prioridade sobre os defaults do ficheiro):
  ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME, ADMIN_WHATSAPP

Se o e-mail já existir: is_admin=True e senha reposta.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.dotenv_loader import load_dotenv

load_dotenv()

# --- Defaults (sobrescreve com variáveis de ambiente ou entradas no .env) ---

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip() or "admin@teu-dominio.com"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip() or "altera-esta-senha-forte"
ADMIN_FULL_NAME = os.environ.get("ADMIN_FULL_NAME", "").strip() or "Administrador"
ADMIN_WHATSAPP = os.environ.get("ADMIN_WHATSAPP", "").strip() or ""

# ----------------------------------------------------------------------


def _config_ok() -> bool:
    if ADMIN_EMAIL.strip().lower() in ("", "admin@teu-dominio.com"):
        return False
    if ADMIN_PASSWORD.strip() == "" or ADMIN_PASSWORD == "altera-esta-senha-forte":
        return False
    if not ADMIN_WHATSAPP.strip():
        return False
    return True


async def _main() -> None:
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
    from app.models import User
    from app.security import hash_password

    if not _config_ok():
        print(
            "Erro: define ADMIN_EMAIL, ADMIN_PASSWORD e ADMIN_WHATSAPP "
            "(variáveis de ambiente ou edita os defaults em scripts/create_admin.py).",
            file=sys.stderr,
        )
        sys.exit(1)

    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    engine = create_async_engine(url, echo=False, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    pwd_hash = hash_password(ADMIN_PASSWORD)

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == ADMIN_EMAIL))
        user = result.scalar_one_or_none()

        if user is None:
            dup_wp = await session.execute(select(User.id).where(User.whatsapp == ADMIN_WHATSAPP))
            if dup_wp.scalar_one_or_none() is not None:
                print(
                    "Erro: ADMIN_WHATSAPP já está em uso por outro utilizador.",
                    file=sys.stderr,
                )
                sys.exit(1)
            session.add(
                User(
                    full_name=ADMIN_FULL_NAME,
                    email=ADMIN_EMAIL,
                    password_hash=pwd_hash,
                    whatsapp=ADMIN_WHATSAPP,
                    balance=Decimal("0.00"),
                    is_admin=True,
                ),
            )
            print(f"Criado administrador: {ADMIN_EMAIL}")
        else:
            user.password_hash = pwd_hash
            user.is_admin = True
            user.full_name = ADMIN_FULL_NAME
            print(f"Actualizado: {ADMIN_EMAIL} (is_admin=True, senha reposta)")

        await session.commit()

    await engine.dispose()
    print("Concluído. Login: POST /auth/login")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
