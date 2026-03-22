#!/usr/bin/env python3
"""
Utilizadores mock para desenvolvimento (opcional).

Schema alinhado a app/models.py: User com balance Decimal, is_admin, whatsapp único.

Não uses isto como substituto de create_admin em produção. Para admin real:
  scripts/create_admin.py

Uso:
  python scripts/seed_test_data.py

Ignora e-mails que já existem (idempotente por e-mail).
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
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
    from app.models import User
    from app.security import hash_password

    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    engine = create_async_engine(url, echo=False, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    admin_email = "admin@apexkeys.example.com"
    user_email = "user@apexkeys.example.com"
    password = "senha12345"
    whatsapp_admin = "+351911111101"
    whatsapp_user = "+351911111102"

    async with session_factory() as session:
        for email, name, wp, is_admin in [
            (admin_email, "Admin Teste", whatsapp_admin, True),
            (user_email, "Usuário Teste", whatsapp_user, False),
        ]:
            result = await session.execute(select(User.id).where(User.email == email))
            if result.scalar_one_or_none() is not None:
                print(f"  {email} já existe, ignorando.")
                continue
            u = User(
                full_name=name,
                email=email,
                password_hash=hash_password(password),
                whatsapp=wp,
                balance=Decimal("0.00"),
                is_admin=is_admin,
            )
            session.add(u)
            await session.flush()
            print(f"  Criado: {email} (admin={is_admin})")
        await session.commit()

    await engine.dispose()
    print("\nDados de teste (.example.com — válido para Pydantic EmailStr):")
    print("  Admin: admin@apexkeys.example.com / senha12345")
    print("  User:  user@apexkeys.example.com / senha12345")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
