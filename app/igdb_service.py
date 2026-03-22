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

_YOUTUBE_ID_IN_URL = re.compile(
    r"(?:[?&]v=|/embed/|\.be/)([a-zA-Z0-9_-]{11})\b",
)
_YOUTUBE_ID_STANDALONE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

IGDB_PUBLIC_GAME_URL = "https://www.igdb.com/games"

_SLUG_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")

# httpx por vezes recebe 200 com página de desafio; não parsear como ficha do jogo.
_OG_IMAGE_CONTENT_FIRST = re.compile(
    r'content\s*=\s*["\'](https://images\.igdb\.com/[^"\']+)["\'][^>]*property\s*=\s*["\']og:image["\']',
    re.I,
)
_OG_IMAGE_PROPERTY_FIRST = re.compile(
    r'property\s*=\s*["\']og:image["\'][^>]*content\s*=\s*["\'](https://images\.igdb\.com/[^"\']+)["\']',
    re.I,
)
# Capa principal em <img> (SSR / hidratação): WebP em t_cover_big.
_COVER_BIG_WEBP_IN_HTML = re.compile(
    r'//images\.igdb\.com/igdb/image/upload/t_cover_big/[^"\'\s<>]+\.webp',
    re.I,
)
_IGDB_UPLOAD_PATH = re.compile(
    r"(https?://images\.igdb\.com/igdb/image/upload/)t_[^/]+/([^/.]+)\.(?:jpg|jpeg|png|webp)",
    re.I,
)


def _html_is_cloudflare_challenge(html: str) -> bool:
    h = (html or "")[:32000].lower()
    if "_cf_chl_opt" in h:
        return True
    if "cf-browser-verification" in h:
        return True
    if "cdn-cgi/challenge-platform" in h and ("just a moment" in h or "turnstile" in h):
        return True
    return False


def _igdb_cdn_cover_from_raw_html(html: str) -> str | None:
    """Fallback quando o BS4 falha: WebP t_cover_big no HTML, senão og:image IGDB."""
    wm = _COVER_BIG_WEBP_IN_HTML.search(html)
    if wm:
        return _abs_url(wm.group(0))
    m = _OG_IMAGE_PROPERTY_FIRST.search(html) or _OG_IMAGE_CONTENT_FIRST.search(html)
    return m.group(1) if m else None


def _derive_t_cover_big_webp(url: str | None) -> str | None:
    """
    A partir de qualquer tamanho IGDB (ex.: og:image t_cover_big_2x .jpg),
    obtém a variante leve t_cover_big/*.webp (mesmo id de ficheiro).
    """
    if not url:
        return None
    m = _IGDB_UPLOAD_PATH.search(url)
    if not m:
        return None
    prefix, image_id = m.group(1), m.group(2)
    return f"{prefix}t_cover_big/{image_id}.webp"


def _youtube_url_from_schema_trailer(trailer: Any) -> str | None:
    """Schema.org: VideoGame.trailer → VideoObject (embedUrl / url)."""
    if trailer is None:
        return None
    blocks: list[Any] = trailer if isinstance(trailer, list) else [trailer]
    for block in blocks:
        if isinstance(block, str):
            yid = _youtube_video_id_from_url(block)
            if yid:
                return f"https://www.youtube.com/watch?v={yid}"
            continue
        if not isinstance(block, dict):
            continue
        for key in ("embedUrl", "contentUrl", "url"):
            val = block.get(key)
            if isinstance(val, str):
                yid = _youtube_video_id_from_url(val)
                if yid:
                    return f"https://www.youtube.com/watch?v={yid}"
    return None


def _best_hero_cover_webp_from_imgs(soup: BeautifulSoup) -> str | None:
    """
    Primeira capa principal em <img>: t_cover_big + .webp na CDN IGDB.
    Prioriza alt com 'cover' (capa do jogo na ficha), depois ordem no DOM.
    """
    scored: list[tuple[int, int, str]] = []
    for idx, img in enumerate(soup.find_all("img")):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src:
            continue
        u = _abs_url(src)
        if not u:
            continue
        lu = u.lower()
        if "images.igdb.com" not in lu or "/t_cover_big/" not in lu:
            continue
        if not lu.endswith(".webp"):
            continue
        alt = (img.get("alt") or "").lower()
        # Alt tipo "Elden Ring cover" — típico da capa hero; sem isso mantemos ordem DOM.
        priority = 0 if "cover" in alt else 1
        scored.append((priority, idx, u))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def _collect_meta_image_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    for tag in soup.find_all("meta"):
        content = tag.get("content")
        if not content:
            continue
        prop = (tag.get("property") or "").strip().lower()
        name = (tag.get("name") or "").strip().lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            u = _abs_url(str(content).strip())
            if u:
                urls.append(u)
        elif name in ("twitter:image", "twitter:image:src"):
            u = _abs_url(str(content).strip())
            if u:
                urls.append(u)
    return urls


def _select_cover_url(candidates: list[str]) -> str | None:
    if not candidates:
        return None

    def is_igdb_cdn(u: str) -> bool:
        lu = u.lower()
        return "images.igdb.com" in lu or "/igdb/image/upload" in lu

    for u in candidates:
        if is_igdb_cdn(u):
            return u
    for u in candidates:
        if "rawg.io" not in u.lower():
            return u
    return candidates[0]


def _youtube_video_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    m = _YOUTUBE_ID_IN_URL.search(u)
    if m:
        return m.group(1)
    if _YOUTUBE_ID_STANDALONE.match(u):
        return u
    return None


def _youtube_maxres_thumbnail(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"


def _youtube_url_from_og_metas(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("meta", attrs={"property": True}):
        prop = str(tag.get("property") or "").strip().lower()
        if "og:video" not in prop:
            continue
        content = tag.get("content")
        if not content:
            continue
        u = _abs_url(str(content).strip())
        yid = _youtube_video_id_from_url(u)
        if yid:
            return f"https://www.youtube.com/watch?v={yid}"
    return None


def _youtube_url_from_dom(soup: BeautifulSoup, html: str) -> str | None:
    for iframe in soup.find_all("iframe"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        u = _abs_url(src)
        yid = _youtube_video_id_from_url(u or src)
        if yid:
            return f"https://www.youtube.com/watch?v={yid}"
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        hlow = href.lower()
        if "youtube" not in hlow and "youtu.be" not in hlow:
            continue
        yid = _youtube_video_id_from_url(href)
        if yid:
            return f"https://www.youtube.com/watch?v={yid}"
    for pat in (
        r"(?:youtube\.com/embed/|youtube-nocookie\.com/embed/)([a-zA-Z0-9_-]{11})\b",
        r'youtube\.com/watch\?[^"\'\s<>]*\bv=([a-zA-Z0-9_-]{11})',
        r"youtu\.be/([a-zA-Z0-9_-]{11})\b",
    ):
        m = re.search(pat, html, re.I)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    return None


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
        cand: str | None = None
        if isinstance(img, str):
            cand = img
        elif isinstance(img, list) and img:
            cand = str(img[0]) if isinstance(img[0], str) else None
        elif isinstance(img, dict) and img.get("url"):
            cand = str(img["url"])
        if cand and not base.get("image_url"):
            u = _abs_url(cand.strip())
            # Schema.org por vezes aponta para outro agregador (ex.: RAWG); na ficha IGDB a capa canónica é og:image.
            if u and "rawg.io" not in u.lower():
                base["image_url"] = u

        if item.get("description") and not base.get("summary"):
            base["summary"] = str(item["description"])[:8000]

        if not base.get("youtube_url"):
            tu = _youtube_url_from_schema_trailer(item.get("trailer"))
            if tu:
                base["youtube_url"] = tu

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

    if not base.get("youtube_url"):
        for item in _iter_ld_json_objects(soup):
            if not isinstance(item, dict):
                continue
            if not (_ld_types(item) & {"VideoObject"}):
                continue
            for key in ("embedUrl", "contentUrl"):
                val = item.get(key)
                if isinstance(val, str):
                    yid = _youtube_video_id_from_url(val)
                    if yid:
                        base["youtube_url"] = f"https://www.youtube.com/watch?v={yid}"
                        break
            if base.get("youtube_url"):
                break

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

    image = _best_hero_cover_webp_from_imgs(soup)
    if not image:
        candidates = _collect_meta_image_urls(soup)
        image = _select_cover_url(candidates)
        if not image or "rawg.io" in image.lower():
            raw_igdb = _igdb_cdn_cover_from_raw_html(html)
            if raw_igdb:
                image = raw_igdb

    if image and "rawg.io" not in image.lower():
        derived = _derive_t_cover_big_webp(image)
        if derived:
            image = derived

    description = meta_prop("og:description") or meta_name("description")
    canonical = _abs_url(meta_prop("og:url")) or f"{IGDB_PUBLIC_GAME_URL}/{fallback_slug}"

    youtube = _youtube_url_from_og_metas(soup)
    if not youtube:
        tw = meta_name("twitter:player")
        if tw:
            u = _abs_url(tw)
            yid_tw = _youtube_video_id_from_url(u)
            if yid_tw:
                youtube = f"https://www.youtube.com/watch?v={yid_tw}"
    if not youtube:
        youtube = _youtube_url_from_dom(soup, html)

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
    base = _enrich_from_ld_json(soup, base)
    yid_final = _youtube_video_id_from_url(base.get("youtube_url"))
    base["youtube_thumbnail_url"] = _youtube_maxres_thumbnail(yid_final) if yid_final else None
    return base


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
            if r.status_code == 200 and r.text and not _html_is_cloudflare_challenge(r.text):
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
