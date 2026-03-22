"""
Scraper público de páginas de jogo em igdb.com/games/<slug>.

Usa **cloudscraper** (com **requests**) para contornar desafios comuns do Cloudflare
e **beautifulsoup4** para o parse. O pedido síncrono corre em thread via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import cloudscraper
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("apex_keys")

IGDB_PUBLIC_GAME_URL = "https://www.igdb.com/games"

_SLUG_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def normalize_igdb_game_url(raw: str) -> tuple[str, str]:
    """
    Valida URL (anti-SSRF: só igdb.com, só /games/<slug>), devolve
    (url_canónica_https, slug).
    """
    text = raw.strip()
    if not text:
        raise ValueError("URL vazia")

    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("https", "http"):
        raise ValueError("A URL deve começar por https://")

    host = (parsed.hostname or "").lower()
    if host not in ("www.igdb.com", "igdb.com"):
        raise ValueError("Apenas URLs do domínio igdb.com são permitidas.")

    path = parsed.path or ""
    path = path.rstrip("/") or "/"
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() != "games":
        raise ValueError("O caminho deve ser /games/<slug> (página de um jogo).")

    slug = segments[1].lower()
    if not _SLUG_SEGMENT.match(slug) or ".." in slug:
        raise ValueError("Slug do jogo na URL é inválido.")

    canonical = f"https://www.igdb.com/games/{slug}"
    return canonical, slug


def _abs_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return f"https://{u.lstrip('/')}"


def _as_text_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        out: list[str] = []
        for x in val:
            if isinstance(x, dict):
                n = x.get("name")
                if n:
                    out.append(str(n))
            else:
                out.append(str(x))
        return [t for t in out if t]
    if isinstance(val, dict) and val.get("name"):
        return [str(val["name"])]
    return [str(val)]


def _iter_ld_json_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    found.append(item)
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        found.append(item)
            else:
                found.append(data)
    return found


def _ld_types(item: dict[str, Any]) -> set[str]:
    t = item.get("@type")
    if t is None:
        return set()
    if isinstance(t, list):
        return {str(x) for x in t}
    return {str(t)}


def _enrich_from_ld_json(soup: BeautifulSoup, base: dict[str, Any]) -> dict[str, Any]:
    """Preenche género / séries / modos se o IGDB expuser schema.org na página."""
    genres = list(base.get("genres") or [])
    series = list(base.get("series") or [])
    game_modes = list(base.get("game_modes") or [])

    interesting = {"VideoGame", "Game", "SoftwareApplication"}

    for item in _iter_ld_json_objects(soup):
        types = _ld_types(item)
        if not types & interesting:
            continue

        genres.extend(_as_text_list(item.get("genre")))
        game_modes.extend(_as_text_list(item.get("playMode")))
        game_modes.extend(_as_text_list(item.get("gameplayMode")))

        ps = item.get("partOfSeries") or item.get("isPartOf")
        if isinstance(ps, dict):
            series.extend(_as_text_list(ps.get("name")))
        else:
            series.extend(_as_text_list(ps))

        img = item.get("image")
        if isinstance(img, str) and not base.get("image_url"):
            base["image_url"] = _abs_url(img)
        elif isinstance(img, list) and img and not base.get("image_url"):
            base["image_url"] = _abs_url(str(img[0]) if isinstance(img[0], str) else None)
        elif isinstance(img, dict) and img.get("url") and not base.get("image_url"):
            base["image_url"] = _abs_url(str(img["url"]))

        if item.get("description") and not base.get("summary"):
            base["summary"] = str(item["description"])[:8000]

    def _uniq(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            k = x.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    base["genres"] = _uniq(genres)
    base["series"] = _uniq(series)
    base["game_modes"] = _uniq(game_modes)
    base["player_perspectives"] = _uniq(list(base.get("player_perspectives") or []))
    return base


def parse_igdb_public_html(html: str, fallback_slug: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    def meta_prop(prop: str) -> str | None:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
        return None

    def meta_name(name: str) -> str | None:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
        return None

    og_title = meta_prop("og:title")
    page_title = None
    if soup.title and soup.title.string:
        page_title = soup.title.string.strip()

    title_line = og_title or page_title or ""
    if title_line.endswith("| IGDB.com"):
        title_line = title_line[: -len("| IGDB.com")].strip()

    image = _abs_url(meta_prop("og:image"))
    description = meta_prop("og:description") or meta_name("description")
    canonical = _abs_url(meta_prop("og:url")) or f"{IGDB_PUBLIC_GAME_URL}/{fallback_slug}"

    youtube = _abs_url(meta_prop("og:video:url")) or _abs_url(meta_prop("og:video"))
    if not youtube:
        tw = meta_name("twitter:player")
        if tw:
            youtube = _abs_url(tw)

    game_id = None
    page_el = soup.find("meta", attrs={"id": "pageid"})
    if page_el and page_el.get("data-game-id"):
        game_id = str(page_el["data-game-id"])

    base: dict[str, Any] = {
        "name": title_line.split("(")[0].strip() if title_line else None,
        "title": title_line or None,
        "slug": fallback_slug,
        "summary": description,
        "image_url": image,
        "youtube_url": youtube,
        "igdb_url": canonical,
        "igdb_game_id": game_id,
        "genres": [],
        "series": [],
        "game_modes": [],
        "player_perspectives": [],
    }
    return _enrich_from_ld_json(soup, base)


def _fetch_public_game_html_sync(url: str) -> tuple[str, int]:
    """GET síncrono com sessão que imita browser (Cloudflare)."""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
    )
    headers = {
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = scraper.get(url, headers=headers, timeout=30)
    return r.text, r.status_code


async def fetch_igdb_page(
    url: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, int]:
    """Obtém HTML da URL já validada (https://www.igdb.com/games/<slug>)."""
    if http_client is not None:
        try:
            r = await http_client.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": "Mozilla/5.0 (compatible; ApexKeys/1.0)",
                },
            )
            if r.status_code == 200 and r.text:
                return r.text, r.status_code
        except httpx.HTTPError:
            pass
    return await asyncio.to_thread(_fetch_public_game_html_sync, url)


async def lookup_igdb_game_url(
    page_url: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Scrape a partir da URL completa da ficha do jogo no IGDB."""
    canonical, slug = normalize_igdb_game_url(page_url)
    html, status = await fetch_igdb_page(canonical, http_client)

    if status == 404 or "doesn't exist" in html.lower() or "page you were looking for" in html.lower():
        raise LookupError(f"jogo não encontrado ou URL inválida (slug: {slug})")

    if status in (403, 503) or "cf-mitigated" in html.lower():
        raise LookupError(
            "não foi possível obter a página do IGDB (bloqueio ou desafio não resolvido). "
            "O cloudscraper reduz falhas de Cloudflare, mas não garante 100% em todos os ambientes."
        )

    parsed = parse_igdb_public_html(html, slug)
    parsed["igdb_url"] = canonical
    if not parsed.get("image_url") and not parsed.get("title"):
        raise LookupError("não foi possível extrair dados úteis do HTML")

    return parsed
