from __future__ import annotations

import asyncio
import logging
import os
import ssl
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

logger = logging.getLogger("apex_keys")

_engine: AsyncEngine | None = None
async_session_maker: async_sessionmaker[AsyncSession] | None = None


def _is_transient_connect_error(exc: BaseException) -> bool:
    """Erros típicos quando o Postgres ainda não aceita ligações (ex.: Railway a arrancar serviços)."""
    if isinstance(exc, ConnectionRefusedError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 111:  # ECONNREFUSED (Linux)
        return True
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return _is_transient_connect_error(cause)
    return False


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
    max_attempts = int(os.getenv("DB_CONNECT_RETRIES", "15"))
    delay = float(os.getenv("DB_CONNECT_RETRY_DELAY_SEC", "1.0"))
    delay_max = float(os.getenv("DB_CONNECT_RETRY_DELAY_MAX_SEC", "8.0"))

    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        eng: AsyncEngine | None = None
        try:
            eng = create_async_engine(url, echo=False, connect_args=connect_args)
            async_session_maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            _engine = eng
            if attempt > 1:
                logger.info("Base de dados disponível após %s tentativas.", attempt)
            return
        except Exception as e:
            last_error = e
            if eng is not None:
                await eng.dispose()
            async_session_maker = None
            if not _is_transient_connect_error(e) or attempt >= max_attempts:
                logger.error(
                    "Falha ao ligar ao Postgres (tentativa %s/%s). "
                    "Na Railway: usa a variável DATABASE_URL do serviço Postgres (referência), "
                    "não localhost. Erro: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                raise
            logger.warning(
                "Postgres indisponível (tentativa %s/%s): %s — nova tentativa em %.1fs",
                attempt,
                max_attempts,
                e,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, delay_max)

    assert last_error is not None
    raise last_error


async def close_db() -> None:
    global _engine, async_session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    async_session_maker = None
