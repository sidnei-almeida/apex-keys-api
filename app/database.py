from __future__ import annotations

import os
import ssl
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

_engine: AsyncEngine | None = None
async_session_maker: async_sessionmaker[AsyncSession] | None = None


def _resolve_database_url() -> str:
    """DATABASE_URL (Neon/Railway) ou settings; normaliza para driver asyncpg."""
    env_url = os.getenv("DATABASE_URL")
    if env_url and env_url.strip():
        raw = env_url.strip()
    else:
        raw = get_settings().database_url.strip()
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def _url_without_sslmode_for_asyncpg(url: str) -> tuple[str, dict]:
    """
    asyncpg não aceita o parâmetro sslmode na URL como keyword de connect();
    remove-o da query e activa SSL via connect_args quando necessário.
    """
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    connect_args: dict = {}
    kept: list[tuple[str, str]] = []
    for k, v in pairs:
        lk = k.lower()
        if lk == "sslmode":
            if v.lower() in ("require", "verify-ca", "verify-full", "allow", "prefer"):
                connect_args["ssl"] = True
            if v.lower() == "disable":
                connect_args.pop("ssl", None)
            continue
        if lk == "ssl" and v.lower() in ("1", "true", "on", "require"):
            connect_args["ssl"] = True
            continue
        kept.append((k, v))
    new_query = urlencode(kept)
    clean = urlunparse(parsed._replace(query=new_query))
    if connect_args.get("ssl") is True:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context
    return clean, connect_args


async def init_db() -> None:
    global _engine, async_session_maker
    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    _engine = create_async_engine(url, echo=False, connect_args=connect_args)
    async_session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global _engine, async_session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    async_session_maker = None
