from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import database


async def get_session() -> AsyncIterator[AsyncSession]:
    if database.async_session_maker is None:
        raise RuntimeError("Base de dados não inicializada")
    async with database.async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        yield client
