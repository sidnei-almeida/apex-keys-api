#!/usr/bin/env python3
"""
Zera o schema `public` e reaplica `schema.sql` (sem precisar do cliente `psql`).
Inclui colunas do Hall da Fama em `raffles` (`winning_ticket_number`, `drawn_at`).

Uso (na raiz do repo, com DATABASE_URL no ambiente ou em .env):
  .venv/bin/python scripts/reset_db.py
  # requer dependência asyncpg (requirements do projeto)

Depois (base vazia):
  1. python scripts/create_admin.py   # ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_WHATSAPP
  2. python scripts/seed_bulk_raffles.py   # povoa rifas a partir do catálogo JSON

Alternativa equivalente ao SQL estático: python scripts/reset_and_apply_schema.py
(recria tabelas via SQLAlchemy a partir de app/models.py).
"""
from __future__ import annotations

import asyncio
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlparse, urlunparse

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "schema.sql"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _dsn_for_asyncpg(raw: str) -> str:
    u = raw.strip()
    if u.startswith("postgresql+asyncpg://"):
        u = "postgresql://" + u[len("postgresql+asyncpg://") :]
    elif u.startswith("postgres://"):
        u = "postgresql://" + u[len("postgres://") :]
    return u


def _asyncpg_connect_args(dsn: str) -> tuple[str, dict]:
    """Remove sslmode da query (asyncpg não usa) e define ssl= quando necessário."""
    parsed = urlparse(dsn)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    ssl_required = False
    kept: list[tuple[str, str]] = []
    for k, v in pairs:
        lk = k.lower()
        if lk == "sslmode" and v.lower() in ("require", "verify-ca", "verify-full", "allow", "prefer"):
            ssl_required = True
            continue
        if lk == "ssl" and v.lower() in ("1", "true", "on", "require"):
            ssl_required = True
            continue
        kept.append((k, v))
    new_q = "&".join(f"{a}={b}" for a, b in kept) if kept else ""
    clean = urlunparse(parsed._replace(query=new_q))
    kw: dict = {}
    if ssl_required:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kw["ssl"] = ctx
    return clean, kw


def _strip_sql_comments(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines)


def _split_statements(sql: str) -> list[str]:
    """Parte por ';' (DDL de referência não inclui ';' dentro de literais)."""
    text = _strip_sql_comments(sql)
    parts: list[str] = []
    for chunk in text.split(";"):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


async def _run() -> None:
    _load_dotenv()
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        print("Erro: defina DATABASE_URL ou crie .env na raiz do apex-keys-api.", file=sys.stderr)
        raise SystemExit(1)
    if not SCHEMA_FILE.is_file():
        print(f"Erro: não encontrei {SCHEMA_FILE}", file=sys.stderr)
        raise SystemExit(1)

    dsn, conn_kw = _asyncpg_connect_args(_dsn_for_asyncpg(raw))
    schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")

    print("A ligar ao Postgres…")
    conn = await asyncpg.connect(dsn, **conn_kw)
    try:
        print("DROP SCHEMA public CASCADE + CREATE…")
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("GRANT ALL ON SCHEMA public TO public")

        stmts = _split_statements(schema_sql)
        print(f"A executar {len(stmts)} instruções de schema.sql…")
        for i, stmt in enumerate(stmts, 1):
            await conn.execute(stmt + ";")
        print("Concluído: base zerada com o schema atual.")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
