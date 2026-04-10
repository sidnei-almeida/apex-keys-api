"""Rankings — posição do utilizador autenticado por categoria."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import User
from app.ranking_me_service import compute_ranking_me
from app.schemas import RankingMeOut
from app.security import get_optional_user_id

router = APIRouter()

CategoryQuery = Literal["victories", "buyers", "active", "hot"]


def _guest_response(category: str) -> RankingMeOut:
    return RankingMeOut(
        authenticated=False,
        category=category,
        rank=None,
        metric_label=None,
        metric_value=None,
        metric_display=None,
        next_target_label=(
            "Faça login para acompanhar sua posição no ranking. "
            "Entre na sua conta para ver seu desempenho em cada categoria."
        ),
        progress_percent=0,
        in_ranking=False,
    )


@router.get("/me", response_model=RankingMeOut)
async def ranking_me(
    category: CategoryQuery = Query(
        ...,
        description="victories | buyers | active | hot",
    ),
    session: AsyncSession = Depends(get_session),
    user_id=Depends(get_optional_user_id),
) -> RankingMeOut:
    if user_id is None:
        return _guest_response(category)

    u_result = await session.execute(select(User).where(User.id == user_id))
    user = u_result.scalar_one_or_none()
    if user is None or user.deactivated_at is not None:
        return _guest_response(category)

    data = await compute_ranking_me(session, user_id, category)
    return RankingMeOut(**data)
