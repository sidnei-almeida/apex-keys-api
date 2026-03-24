#!/usr/bin/env python3
"""
Insere rifas de teste a partir de um catálogo JSON (imagens + trailers).

Ficheiro: scripts/bulk_raffles_catalog.json — edita image_url e trailer_youtube
por jogo (URL do YouTube completa ou só o video_id de 11 caracteres).

Usa tactical_ticket_price como a API. O título na BD é o mesmo «title» do JSON.

--clear apaga: (1) rifas antigas com prefixo [bulk-dev] ; (2) rifas cujo título
coincide exactamente com algum «title» do catálogo (para repovoar sem duplicar).

Uso:
  python scripts/seed_bulk_raffles.py
  python scripts/seed_bulk_raffles.py --count 5 --gold 1
  python scripts/seed_bulk_raffles.py --catalog caminho/outro.json
  python scripts/seed_bulk_raffles.py --clear --count 0
  python scripts/seed_bulk_raffles.py --update-media   # só preenche capa + vídeo nas rifas já criadas

Requer DATABASE_URL no .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.dotenv_loader import load_dotenv

# Rifas criadas por versões antigas do script (para --clear as remover)
LEGACY_BULK_PREFIX = "[bulk-dev] "
_SCRIPT_DIR = Path(__file__).resolve().parent


def _default_catalog_path() -> Path:
    """URLs reais: guarda em bulk_raffles_catalog.local.json (gitignored)."""
    local = _SCRIPT_DIR / "bulk_raffles_catalog.local.json"
    if local.is_file():
        return local
    return _SCRIPT_DIR / "bulk_raffles_catalog.json"

_YOUTUBE_ID_RE = re.compile(
    r"(?:[?&]v=|/embed/|youtu\.be/|/shorts/)([a-zA-Z0-9_-]{11})\b",
)

_IMAGE_KEYS = (
    "image_url",
    "cover_url",
    "thumbnail_url",
    "img_url",
    "image",
    "cover",
    "capa",
    "poster",
)
_TRAILER_KEYS = (
    "trailer_youtube",
    "youtube_url",
    "youtube",
    "trailer",
    "trailer_url",
    "video_url",
)


def _row_keys_lower(row: dict) -> dict[str, Any]:
    return {str(k).lower().replace("-", "_"): v for k, v in row.items() if isinstance(k, str)}


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _image_from_row(row: dict) -> str | None:
    lk = _row_keys_lower(row)
    for key in _IMAGE_KEYS:
        s = _norm_str(lk.get(key))
        if s:
            return s[:1024]
    return None


def _youtube_id_from_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    m = _YOUTUBE_ID_RE.search(u)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", u):
        return u
    return None


def _trailer_from_row(row: dict) -> str | None:
    lk = _row_keys_lower(row)
    for key in _TRAILER_KEYS:
        s = _norm_str(lk.get(key))
        if s:
            vid = _youtube_id_from_url(s)
            if vid:
                return vid
    s = _norm_str(lk.get("video_id"))
    if s:
        return _youtube_id_from_url(s)
    return None


def _resolve_catalog_path(p: Path) -> Path:
    """Aceita caminho absoluto ou relativo a scripts/, raiz do repo ou cwd."""
    if p.is_file():
        return p.resolve()
    for base in (_SCRIPT_DIR, _ROOT, Path.cwd()):
        c = (base / p).resolve()
        if c.is_file():
            return c
    return (_SCRIPT_DIR / p.name).resolve() if not p.is_absolute() else p.resolve()


def _title_from_row(row: dict) -> str:
    lk = _row_keys_lower(row)
    for key in ("title", "titulo", "name", "jogo"):
        s = _norm_str(lk.get(key))
        if s:
            return s
    return _norm_str(row.get("title"))


def _load_games(catalog_path: Path) -> list[dict[str, Any]]:
    if not catalog_path.is_file():
        print(f"Erro: catálogo não encontrado: {catalog_path}", file=sys.stderr)
        sys.exit(1)
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        games = raw
    else:
        games = raw.get("games")
    if not isinstance(games, list) or not games:
        print("Erro: o JSON deve ter uma chave «games» com lista de objetos.", file=sys.stderr)
        sys.exit(1)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(games):
        if not isinstance(row, dict):
            continue
        title = _title_from_row(row)
        if not title:
            print(f"Aviso: entrada #{i + 1} sem title — ignorada.", file=sys.stderr)
            continue
        img = _image_from_row(row)
        vid = _trailer_from_row(row)
        out.append({"title": title, "image_url": img, "video_id": vid})
    if not out:
        print("Erro: nenhuma entrada válida no catálogo.", file=sys.stderr)
        sys.exit(1)
    return out


def _tier_for_index(i: int, gold: int, rest: str) -> str:
    if i < gold:
        return "featured"
    return "carousel" if rest == "carousel" else "none"


def _price_bundle(i: int) -> tuple[Decimal, int]:
    tickets = 80 + (i * 23) % 420
    per = Decimal("3.49") + Decimal((i * 7) % 45) / Decimal("10")
    total = (per * tickets).quantize(Decimal("0.01"))
    if total <= 0:
        total, tickets = Decimal("199.90"), 100
    return total, int(tickets)


async def _clear_bulk(
    session,
    Ticket,
    Raffle,
    catalog_titles: frozenset[str],
) -> int:
    from sqlalchemy import delete, or_, select

    conds = [Raffle.title.startswith(LEGACY_BULK_PREFIX)]
    if catalog_titles:
        conds.append(Raffle.title.in_(catalog_titles))
    r_result = await session.execute(select(Raffle.id).where(or_(*conds)))
    ids = [row[0] for row in r_result.all()]
    if not ids:
        return 0
    await session.execute(delete(Ticket).where(Ticket.raffle_id.in_(ids)))
    await session.execute(delete(Raffle).where(Raffle.id.in_(ids)))
    return len(ids)


async def _main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Seed de rifas a partir de bulk_raffles_catalog.json")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="JSON do catálogo (default: bulk_raffles_catalog.local.json se existir, senão bulk_raffles_catalog.json)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Quantas rifas criar (primeiras N do catálogo). Omite para usar todas as entradas.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Apaga rifas «[bulk-dev] …» antigas e rifas cujo título = algum do catálogo",
    )
    parser.add_argument("--gold", type=int, default=2, help="Primeiras N rifas em featured (default: 2)")
    parser.add_argument(
        "--rest",
        choices=("carousel", "none"),
        default="carousel",
        help="Tier do restante (default: carousel)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Só cria entradas que tenham image_url e trailer_youtube preenchidos",
    )
    parser.add_argument(
        "--update-media",
        action="store_true",
        help="Não recria rifas: actualiza só image_url e video_id onde o título coincide com o catálogo",
    )
    args = parser.parse_args()

    if args.update_media and args.clear:
        parser.error("Não combines --update-media com --clear. Para tudo novo: só --clear.")

    catalog_path = _resolve_catalog_path(args.catalog or _default_catalog_path())

    games = _load_games(catalog_path)
    if args.strict:
        games = [g for g in games if g["image_url"] and g["video_id"]]
        if not games:
            print("Erro: --strict mas nenhuma entrada com imagem e trailer.", file=sys.stderr)
            sys.exit(1)

    n_with_media = sum(1 for g in games if g["image_url"] and g["video_id"])
    if len(games) > 0 and n_with_media == 0:
        print(
            "Aviso: nenhuma entrada do catálogo tem image + trailer reconhecidos. "
            "Chaves aceites: image_url, cover_url, trailer_youtube, youtube_url, video_id, … "
            f"Ficheiro lido: {catalog_path}",
            file=sys.stderr,
        )

    if args.update_media:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
        from app.models import Raffle

        url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
        engine = create_async_engine(url, echo=False, connect_args=connect_args)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        limit = len(games) if args.count is None else min(args.count, len(games))
        slice_games = games[:limit]
        updated = 0
        not_found = 0
        async with session_factory() as session:
            for g in slice_games:
                result = await session.execute(select(Raffle).where(Raffle.title == g["title"]))
                raffle = result.scalar_one_or_none()
                if raffle is None:
                    not_found += 1
                    print(f"Aviso: rifa não encontrada na BD — {g['title']}", file=sys.stderr)
                    continue
                raffle.image_url = g["image_url"]
                raffle.video_id = g["video_id"]
                updated += 1
            await session.commit()
        await engine.dispose()
        print(
            f"Actualizadas {updated} rifas com capa/vídeo do catálogo. "
            f"{not_found} título(s) sem correspondência.",
        )
        return

    total_available = len(games)
    if args.count is None:
        n_create = total_available
    else:
        n_create = args.count

    if n_create < 0 or n_create > 500:
        print("Erro: --count deve estar entre 0 e 500.", file=sys.stderr)
        sys.exit(1)
    if n_create == 0 and not args.clear and not args.update_media:
        print("Nada a fazer: --count 0 sem --clear.", file=sys.stderr)
        sys.exit(1)
    if n_create > total_available:
        print(
            f"Aviso: pediste {n_create} rifas mas o catálogo tem {total_available}; vão ser criadas {total_available}.",
            file=sys.stderr,
        )
        n_create = total_available

    if args.gold < 0 or (n_create > 0 and args.gold > n_create):
        print("Erro: --gold inválido para a quantidade a criar.", file=sys.stderr)
        sys.exit(1)

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import _resolve_database_url, _url_without_sslmode_for_asyncpg
    from app.models import Raffle, RaffleStatus, Ticket
    from app.pricing import tactical_ticket_price

    url, connect_args = _url_without_sslmode_for_asyncpg(_resolve_database_url())
    engine = create_async_engine(url, echo=False, connect_args=connect_args)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    catalog_titles = frozenset(g["title"] for g in games)

    async with session_factory() as session:
        if args.clear:
            removed = await _clear_bulk(session, Ticket, Raffle, catalog_titles)
            await session.commit()
            print(f"Removidas {removed} rifa(s) do catálogo / legado [bulk-dev] e respetivos bilhetes.")

        created = 0
        if n_create > 0:
            slice_games = games[:n_create]
            for i, g in enumerate(slice_games):
                title = g["title"][:255]
                total_price, total_tickets = _price_bundle(i)
                ticket_price = tactical_ticket_price(total_price, total_tickets)
                tier = _tier_for_index(i, args.gold, args.rest)
                if not g["image_url"]:
                    print(f"Aviso: sem image_url — {g['title']}", file=sys.stderr)
                if not g["video_id"]:
                    print(f"Aviso: sem trailer_youtube — {g['title']}", file=sys.stderr)
                session.add(
                    Raffle(
                        title=title,
                        image_url=g["image_url"],
                        video_id=g["video_id"],
                        total_price=total_price,
                        total_tickets=total_tickets,
                        ticket_price=ticket_price,
                        status=RaffleStatus.active.value,
                        featured_tier=tier,
                    ),
                )
                created += 1
            await session.commit()

    await engine.dispose()
    if n_create > 0:
        print(
            f"Criadas {created} rifas a partir de {catalog_path.name}: "
            f"{min(args.gold, created)} featured + {max(0, created - args.gold)} {args.rest}.",
        )
        print("Para repor: python scripts/seed_bulk_raffles.py --clear (remove pelo título do catálogo).")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except json.JSONDecodeError as e:
        print(f"Erro JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Erro: {e}", file=sys.stderr)
        sys.exit(1)
