from __future__ import annotations

import asyncio
import logging
import os
import ssl
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.account_deletion import purge_due_deletions
from app.models import Base

logger = logging.getLogger("apex_keys")

_engine: AsyncEngine | None = None
async_session_maker: async_sessionmaker[AsyncSession] | None = None


def _running_on_railway() -> bool:
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID"),
    )


def _first_nonempty_env(*keys: str) -> str | None:
    for key in keys:
        v = os.getenv(key)
        if v and v.strip():
            return v.strip()
    return None


def _env_database_dsn() -> str | None:
    """Railway: preferir URL privada entre serviços; localmente costuma ser só DATABASE_URL."""
    if _running_on_railway():
        return _first_nonempty_env("DATABASE_PRIVATE_URL", "DATABASE_URL", "POSTGRES_URL")
    return _first_nonempty_env("DATABASE_URL", "DATABASE_PRIVATE_URL", "POSTGRES_URL")


def _parsed_pg_target(dsn: str) -> tuple[str, int, str | None]:
    """(hostname, port, username) para logs — sem password."""
    u = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(u)
    host = parsed.hostname or "(sem host)"
    port = parsed.port or 5432
    return host, port, parsed.username


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
    """DATABASE_URL / DATABASE_PRIVATE_URL / POSTGRES_URL ou settings; normaliza para asyncpg."""
    raw = _env_database_dsn()
    if raw is None:
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

    if _running_on_railway() and _env_database_dsn() is None:
        msg = (
            "DATABASE_URL não está definida no serviço da API. Na Railway: abre o serviço da API → "
            "Variables → + New Variable → tab Reference → escolhe o Postgres → "
            "DATABASE_URL (ou DATABASE_PRIVATE_URL para rede privada)."
        )
        logger.error(msg)
        raise RuntimeError(msg)

    resolved = _resolve_database_url()
    host, port, user = _parsed_pg_target(resolved)
    if _env_database_dsn() is None:
        logger.error(
            "Nenhuma variável DATABASE_URL / DATABASE_PRIVATE_URL / POSTGRES_URL — a usar default "
            "de config: host=%s port=%s (normalmente falha em container).",
            host,
            port,
        )
    else:
        logger.info("Postgres alvo: %s:%s user=%s", host, port, user or "?")
        if _running_on_railway() and host in ("127.0.0.1", "localhost", "::1"):
            logger.error(
                "DATABASE_URL aponta para %s — na Railway o Postgres não corre no mesmo container. "
                "Substitui por uma variável Reference ao serviço Postgres.",
                host,
            )

    url, connect_args = _url_without_sslmode_for_asyncpg(resolved)
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
                # Migração leve (sem Alembic): garantir colunas para desativação/exclusão agendada.
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ NULL",
                    ),
                )
                await conn.execute(
                    text(
                        "ALTER TABLE IF EXISTS users "
                        "ADD COLUMN IF NOT EXISTS delete_after TIMESTAMPTZ NULL",
                    ),
                )
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_users_delete_after ON users (delete_after)",
                    ),
                )
            _engine = eng
            # Purge de contas vencidas (30 dias) no arranque.
            try:
                async with async_session_maker() as s:
                    n = await purge_due_deletions(s)
                    if n:
                        await s.commit()
                        logger.info("Purge de contas: %s utilizador(es) removido(s).", n)
            except Exception as e:
                logger.warning("Falha ao purgar contas vencidas: %s", e)
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
    logger.error(
        "Esgotadas as tentativas de ligação a %s:%s — confirma DATABASE_URL (Reference ao Postgres) e que o Postgres está Running.",
        host,
        port,
    )
    raise last_error


async def close_db() -> None:
    global _engine, async_session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    async_session_maker = None
