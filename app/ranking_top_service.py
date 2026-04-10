"""
Top N utilizadores por categoria para o pódio do Hall da Fama (dados reais).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, desc, func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Raffle, RaffleStatus, Ticket, User
from app.ranking_me_service import (
    RankingCategory,
    _brl,
    _finished_winner_ticket,
    _metric_display,
)

LIMIT_DEFAULT = 5


async def _spotlight_latest_win(session: AsyncSession, user_id: UUID) -> tuple[Raffle, int] | None:
    spot_r = await session.execute(
        select(Raffle, Ticket.ticket_number)
        .join(
            Ticket,
            and_(
                Ticket.raffle_id == Raffle.id,
                Ticket.user_id == user_id,
                Ticket.status == "paid",
                Ticket.ticket_number == Raffle.winning_ticket_number,
            ),
        )
        .where(
            Raffle.status == RaffleStatus.finished.value,
            Raffle.winning_ticket_number.isnot(None),
        )
        .order_by(nulls_last(desc(Raffle.drawn_at)), desc(Raffle.created_at))
        .limit(1),
    )
    row = spot_r.first()
    if row is None:
        return None
    raffle, ticket_number = row[0], int(row[1])
    if raffle.winning_ticket_number is None:
        return None
    return raffle, ticket_number


async def _spotlight_last_paid(session: AsyncSession, user_id: UUID) -> tuple[Raffle, int] | None:
    r = await session.execute(
        select(Raffle, Ticket.ticket_number)
        .join(Ticket, Ticket.raffle_id == Raffle.id)
        .where(Ticket.user_id == user_id, Ticket.status == "paid")
        .order_by(desc(Ticket.created_at))
        .limit(1),
    )
    row = r.first()
    if row is None:
        return None
    return row[0], int(row[1])


async def _spotlight_hot_win(session: AsyncSession, user_id: UUID) -> tuple[Raffle, int] | None:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    drawn_ok = func.coalesce(Raffle.drawn_at, Raffle.created_at) >= since
    spot_r = await session.execute(
        select(Raffle, Ticket.ticket_number)
        .join(
            Ticket,
            and_(
                Ticket.raffle_id == Raffle.id,
                Ticket.user_id == user_id,
                Ticket.status == "paid",
                Ticket.ticket_number == Raffle.winning_ticket_number,
            ),
        )
        .where(
            Raffle.status == RaffleStatus.finished.value,
            Raffle.winning_ticket_number.isnot(None),
            drawn_ok,
        )
        .order_by(nulls_last(desc(Raffle.drawn_at)), desc(Raffle.created_at))
        .limit(1),
    )
    row = spot_r.first()
    if row is None:
        return None
    raffle, ticket_number = row[0], int(row[1])
    if raffle.winning_ticket_number is None:
        return None
    return raffle, ticket_number


def _spotlight_schema(raffle: Raffle, ticket_number: int) -> dict:
    return {
        "raffle_id": raffle.id,
        "title": raffle.title,
        "image_url": raffle.image_url,
        "winning_ticket_number": ticket_number,
    }


async def compute_ranking_top(
    session: AsyncSession,
    category: RankingCategory,
    limit: int = LIMIT_DEFAULT,
) -> list[dict]:
    """Devolve lista de dicts compatíveis com RankingPodiumEntryOut."""

    if category == "victories":
        win_count = func.count().label("win_count")
        top_stmt = (
            select(Ticket.user_id, win_count)
            .join(Raffle, Raffle.id == Ticket.raffle_id)
            .where(
                Raffle.status == RaffleStatus.finished.value,
                Raffle.winning_ticket_number.isnot(None),
                Raffle.winning_ticket_number == Ticket.ticket_number,
                Ticket.status == "paid",
            )
            .group_by(Ticket.user_id)
            .order_by(win_count.desc())
            .limit(limit)
        )
    elif category == "buyers":
        spend_sub = (
            select(
                Ticket.user_id.label("uid"),
                func.sum(Raffle.ticket_price).label("metric"),
            )
            .join(Raffle, Ticket.raffle_id == Raffle.id)
            .where(Ticket.status == "paid")
            .group_by(Ticket.user_id)
        ).subquery()
        top_stmt = (
            select(spend_sub.c.uid, spend_sub.c.metric)
            .order_by(spend_sub.c.metric.desc())
            .limit(limit)
        )
    elif category == "active":
        active_sub = (
            select(
                Ticket.user_id.label("uid"),
                func.count(func.distinct(Ticket.raffle_id)).label("metric"),
            )
            .where(Ticket.status == "paid")
            .group_by(Ticket.user_id)
        ).subquery()
        top_stmt = (
            select(active_sub.c.uid, active_sub.c.metric)
            .order_by(active_sub.c.metric.desc())
            .limit(limit)
        )
    else:
        since = datetime.now(timezone.utc) - timedelta(days=90)
        drawn_ok = func.coalesce(Raffle.drawn_at, Raffle.created_at) >= since
        hot_sub = (
            select(Ticket.user_id.label("uid"), func.count().label("metric"))
            .join(Raffle, Ticket.raffle_id == Raffle.id)
            .where(_finished_winner_ticket, drawn_ok)
            .group_by(Ticket.user_id)
        ).subquery()
        top_stmt = (
            select(hot_sub.c.uid, hot_sub.c.metric)
            .order_by(hot_sub.c.metric.desc())
            .limit(limit)
        )

    top_result = await session.execute(top_stmt)
    top_rows = top_result.all()
    if not top_rows:
        return []

    user_ids = [row[0] for row in top_rows]
    users_r = await session.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in users_r.scalars().all()}

    out: list[dict] = []
    rank = 0
    for row in top_rows:
        uid, raw_metric = row[0], row[1]
        user = users_by_id.get(uid)
        if user is None:
            continue

        if category == "victories":
            metric_int = int(raw_metric or 0)
            if metric_int < 1:
                continue
            stat_line = "1× Vencedor" if metric_int == 1 else f"{metric_int}× Vencedor"
            spot = await _spotlight_latest_win(session, uid)
            if spot is None:
                paid = await _spotlight_last_paid(session, uid)
                spotlight = _spotlight_schema(paid[0], paid[1]) if paid else None
            else:
                spotlight = _spotlight_schema(spot[0], spot[1])
        elif category == "buyers":
            metric_dec = Decimal(raw_metric or 0)
            if metric_dec <= 0:
                continue
            stat_line = f"{_brl(metric_dec)} em rifas"
            paid = await _spotlight_last_paid(session, uid)
            spotlight = _spotlight_schema(paid[0], paid[1]) if paid else None
        elif category == "active":
            metric_int = int(raw_metric or 0)
            if metric_int < 1:
                continue
            stat_line = _metric_display("active", metric_int)
            paid = await _spotlight_last_paid(session, uid)
            spotlight = _spotlight_schema(paid[0], paid[1]) if paid else None
        else:
            metric_int = int(raw_metric or 0)
            if metric_int < 1:
                continue
            stat_line = _metric_display("hot", metric_int)
            spot = await _spotlight_hot_win(session, uid)
            if spot is None:
                paid = await _spotlight_last_paid(session, uid)
                spotlight = _spotlight_schema(paid[0], paid[1]) if paid else None
            else:
                spotlight = _spotlight_schema(spot[0], spot[1])

        rank += 1
        out.append(
            {
                "rank": rank,
                "user_id": user.id,
                "full_name": user.full_name,
                "avatar_url": user.avatar_url,
                "stat_line": stat_line,
                "spotlight": spotlight,
            },
        )

    return out
