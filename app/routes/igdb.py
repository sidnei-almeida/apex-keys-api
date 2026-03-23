import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_http_client
from app.igdb_service import lookup_igdb_game_url
from app.schemas import IgdbGameInfoResponse, IgdbGameUrlRequest

logger = logging.getLogger("apex_keys")

router = APIRouter()


@router.post("/game", response_model=IgdbGameInfoResponse)
async def scrape_igdb_game(
    body: IgdbGameUrlRequest,
    http_client: httpx.AsyncClient = Depends(get_http_client),
) -> IgdbGameInfoResponse:
    """
    Recebe a **URL completa** da ficha do jogo no IGDB (ex.: copiada da barra de endereço)
    e devolve metadados extraídos por scraping (tentativa httpx, fallback cloudscraper + BS4).
    """
    try:
        data = await lookup_igdb_game_url(body.url, http_client)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except LookupError as e:
        msg = str(e)
        if "não foi possível obter a página" in msg:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=msg) from e
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from e
    except httpx.HTTPError as e:
        logger.exception("Erro httpx ao contactar IGDB: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao contactar o site IGDB.",
        ) from e
    except Exception as e:
        logger.exception("Erro de rede ao contactar IGDB: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao contactar o site IGDB.",
        ) from e

    slug = str(data.get("slug") or "")
    return IgdbGameInfoResponse(
        slug=slug,
        name=data.get("name"),
        title=data.get("title"),
        summary=data.get("summary"),
        igdb_url=str(data.get("igdb_url") or ""),
        igdb_game_id=data.get("igdb_game_id"),
        genres=list(data.get("genres") or []),
        series=list(data.get("series") or []),
        game_modes=list(data.get("game_modes") or []),
        player_perspectives=list(data.get("player_perspectives") or []),
    )
