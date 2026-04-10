"""
Posição e métricas do utilizador por categoria (dados reais em tickets + raffles).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Raffle, RaffleStatus, Ticket

RankingCategory = Literal["victories", "buyers", "active", "hot"]

TOP_N = 20

_finished_winner_ticket = and_(
    Raffle.status == RaffleStatus.finished.value,
    Raffle.winning_ticket_number.isnot(None),
    Raffle.winning_ticket_number == Ticket.ticket_number,
    Ticket.status == "paid",
)


def _brl(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"))
    s = f"{q:,.2f}"
    return "R$ " + s.replace(",", "v").replace(".", ",").replace("v", ".")


def _progress_pct(
    rank: int | None,
    my_metric: float,
    threshold: float | None,
) -> int:
    if rank is None or my_metric <= 0:
        return 0
    if rank <= TOP_N:
        return 100
    if threshold is None or threshold <= 0:
        return min(99, 50)
    if my_metric >= threshold:
        return min(99, 95)
    return int(min(99, max(1, round(100 * my_metric / threshold))))


def _next_target_message(
    category: RankingCategory,
    rank: int | None,
    metric_num: int | Decimal,
    threshold: Decimal | int | None,
) -> str:
    zero = (
        rank is None
        or (isinstance(metric_num, int) and metric_num < 1)
        or (isinstance(metric_num, Decimal) and metric_num <= 0)
    )
    if zero:
        return "Você ainda não entrou neste ranking. Participe para começar a subir."

    assert rank is not None

    if rank <= TOP_N:
        return "Você está entre os líderes deste ranking."

    if threshold is None:
        return "Continue participando para melhorar a sua posição."

    if category == "victories":
        m, th = int(metric_num), int(threshold)
        gap = max(0, th - m)
        if gap <= 0:
            return "Continue a participar para consolidar a sua posição no Top 20."
        return f"Faltam {gap} vitória{'s' if gap != 1 else ''} para entrar no Top {TOP_N}."

    if category == "buyers":
        m = metric_num if isinstance(metric_num, Decimal) else Decimal(metric_num)
        th = threshold if isinstance(threshold, Decimal) else Decimal(threshold)
        gap = th - m
        if gap <= 0:
            return "Continue a participar para consolidar a sua posição no Top 20."
        return f"Faltam {_brl(gap)} em compras para entrar no Top {TOP_N}."

    if category == "active":
        m, th = int(metric_num), int(threshold)
        gap = max(0, th - m)
        if gap <= 0:
            return "Continue a participar para consolidar a sua posição no Top 20."
        return f"Faltam {gap} rifa{'s' if gap != 1 else ''} com participação para entrar no Top {TOP_N}."

    m, th = int(metric_num), int(threshold)
    gap = max(0, th - m)
    if gap <= 0:
        return "Continue a participar para consolidar a sua posição no Top 20."
    return f"Faltam {gap} vitória{'s' if gap != 1 else ''} recentes para entrar no Top {TOP_N}."


async def _user_rank_and_metric_wins(
    session: AsyncSession, user_id: UUID
) -> tuple[int | None, int]:
    my_c = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(Ticket.user_id == user_id, _finished_winner_ticket),
    )
    my_metric = int(my_c.scalar_one() or 0)
    if my_metric < 1:
        return None, my_metric

    wins_sub = (
        select(Ticket.user_id.label("uid"), func.count().label("metric"))
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(_finished_winner_ticket)
        .group_by(Ticket.user_id)
    ).subquery()

    ranked = (
        select(
            wins_sub.c.uid,
            wins_sub.c.metric,
            func.rank().over(order_by=wins_sub.c.metric.desc()).label("rnk"),
        )
    ).subquery()

    rr = await session.execute(select(ranked.c.rnk).where(ranked.c.uid == user_id))
    rnk = rr.scalar_one_or_none()
    return (int(rnk) if rnk is not None else None), my_metric


async def _threshold_wins(session: AsyncSession) -> int | None:
    wins_sub = (
        select(Ticket.user_id.label("uid"), func.count().label("metric"))
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(_finished_winner_ticket)
        .group_by(Ticket.user_id)
    ).subquery()
    rn_sub = (
        select(
            wins_sub.c.metric,
            func.row_number().over(order_by=wins_sub.c.metric.desc()).label("rn"),
        )
    ).subquery()
    r = await session.execute(select(rn_sub.c.metric).where(rn_sub.c.rn == TOP_N))
    v = r.scalar_one_or_none()
    return int(v) if v is not None else None


async def _user_rank_and_metric_buyers(
    session: AsyncSession, user_id: UUID
) -> tuple[int | None, Decimal]:
    my_r = await session.execute(
        select(func.coalesce(func.sum(Raffle.ticket_price), 0))
        .select_from(Ticket)
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(Ticket.user_id == user_id, Ticket.status == "paid"),
    )
    my_metric = Decimal(my_r.scalar_one() or 0)
    if my_metric <= 0:
        return None, my_metric

    spend_sub = (
        select(
            Ticket.user_id.label("uid"),
            func.sum(Raffle.ticket_price).label("metric"),
        )
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(Ticket.status == "paid")
        .group_by(Ticket.user_id)
    ).subquery()

    ranked = (
        select(
            spend_sub.c.uid,
            spend_sub.c.metric,
            func.rank().over(order_by=spend_sub.c.metric.desc()).label("rnk"),
        )
    ).subquery()

    rr = await session.execute(select(ranked.c.rnk).where(ranked.c.uid == user_id))
    rnk = rr.scalar_one_or_none()
    return (int(rnk) if rnk is not None else None), my_metric


async def _threshold_buyers(session: AsyncSession) -> Decimal | None:
    spend_sub = (
        select(
            Ticket.user_id.label("uid"),
            func.sum(Raffle.ticket_price).label("metric"),
        )
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(Ticket.status == "paid")
        .group_by(Ticket.user_id)
    ).subquery()
    rn_sub = (
        select(
            spend_sub.c.metric,
            func.row_number().over(order_by=spend_sub.c.metric.desc()).label("rn"),
        )
    ).subquery()
    r = await session.execute(select(rn_sub.c.metric).where(rn_sub.c.rn == TOP_N))
    v = r.scalar_one_or_none()
    return Decimal(v) if v is not None else None


async def _user_rank_and_metric_active(
    session: AsyncSession, user_id: UUID
) -> tuple[int | None, int]:
    my_r = await session.execute(
        select(func.count(func.distinct(Ticket.raffle_id))).where(
            Ticket.user_id == user_id,
            Ticket.status == "paid",
        ),
    )
    my_metric = int(my_r.scalar_one() or 0)
    if my_metric < 1:
        return None, my_metric

    active_sub = (
        select(
            Ticket.user_id.label("uid"),
            func.count(func.distinct(Ticket.raffle_id)).label("metric"),
        )
        .where(Ticket.status == "paid")
        .group_by(Ticket.user_id)
    ).subquery()

    ranked = (
        select(
            active_sub.c.uid,
            active_sub.c.metric,
            func.rank().over(order_by=active_sub.c.metric.desc()).label("rnk"),
        )
    ).subquery()

    rr = await session.execute(select(ranked.c.rnk).where(ranked.c.uid == user_id))
    rnk = rr.scalar_one_or_none()
    return (int(rnk) if rnk is not None else None), my_metric


async def _threshold_active(session: AsyncSession) -> int | None:
    active_sub = (
        select(
            Ticket.user_id.label("uid"),
            func.count(func.distinct(Ticket.raffle_id)).label("metric"),
        )
        .where(Ticket.status == "paid")
        .group_by(Ticket.user_id)
    ).subquery()
    rn_sub = (
        select(
            active_sub.c.metric,
            func.row_number().over(order_by=active_sub.c.metric.desc()).label("rn"),
        )
    ).subquery()
    r = await session.execute(select(rn_sub.c.metric).where(rn_sub.c.rn == TOP_N))
    v = r.scalar_one_or_none()
    return int(v) if v is not None else None


async def _user_rank_and_metric_hot(
    session: AsyncSession, user_id: UUID
) -> tuple[int | None, int]:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    drawn_ok = func.coalesce(Raffle.drawn_at, Raffle.created_at) >= since

    my_c = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(
            Ticket.user_id == user_id,
            _finished_winner_ticket,
            drawn_ok,
        ),
    )
    my_metric = int(my_c.scalar_one() or 0)
    if my_metric < 1:
        return None, my_metric

    hot_sub = (
        select(Ticket.user_id.label("uid"), func.count().label("metric"))
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(_finished_winner_ticket, drawn_ok)
        .group_by(Ticket.user_id)
    ).subquery()

    ranked = (
        select(
            hot_sub.c.uid,
            hot_sub.c.metric,
            func.rank().over(order_by=hot_sub.c.metric.desc()).label("rnk"),
        )
    ).subquery()

    rr = await session.execute(select(ranked.c.rnk).where(ranked.c.uid == user_id))
    rnk = rr.scalar_one_or_none()
    return (int(rnk) if rnk is not None else None), my_metric


async def _threshold_hot(session: AsyncSession) -> int | None:
    since = datetime.now(timezone.utc) - timedelta(days=90)
    drawn_ok = func.coalesce(Raffle.drawn_at, Raffle.created_at) >= since
    hot_sub = (
        select(Ticket.user_id.label("uid"), func.count().label("metric"))
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(_finished_winner_ticket, drawn_ok)
        .group_by(Ticket.user_id)
    ).subquery()
    rn_sub = (
        select(
            hot_sub.c.metric,
            func.row_number().over(order_by=hot_sub.c.metric.desc()).label("rn"),
        )
    ).subquery()
    r = await session.execute(select(rn_sub.c.metric).where(rn_sub.c.rn == TOP_N))
    v = r.scalar_one_or_none()
    return int(v) if v is not None else None


def _metric_display(category: RankingCategory, metric_num: int | Decimal) -> str:
    if category == "buyers":
        m = metric_num if isinstance(metric_num, Decimal) else Decimal(metric_num)
        return _brl(m)
    m = int(metric_num)
    if category == "active":
        return f"{m} rifa{'s' if m != 1 else ''}"
    if category == "hot":
        return f"{m} vitória{'s' if m != 1 else ''} (90 dias)"
    return f"{m} vitória{'s' if m != 1 else ''}"


def _metric_label(category: RankingCategory) -> str:
    if category == "victories":
        return "vitórias"
    if category == "buyers":
        return "valor em rifas"
    if category == "active":
        return "rifas participadas"
    return "vitórias recentes"


async def compute_ranking_me(
    session: AsyncSession,
    user_id: UUID,
    category: RankingCategory,
) -> dict:
    threshold: Decimal | int | None = None

    if category == "victories":
        rank, metric_num = await _user_rank_and_metric_wins(session, user_id)
        threshold = await _threshold_wins(session)
    elif category == "buyers":
        rank, metric_num = await _user_rank_and_metric_buyers(session, user_id)
        threshold = await _threshold_buyers(session)
    elif category == "active":
        rank, metric_num = await _user_rank_and_metric_active(session, user_id)
        threshold = await _threshold_active(session)
    else:
        rank, metric_num = await _user_rank_and_metric_hot(session, user_id)
        threshold = await _threshold_hot(session)

    in_ranking = rank is not None and (
        (isinstance(metric_num, int) and metric_num > 0)
        or (isinstance(metric_num, Decimal) and metric_num > 0)
    )

    ml = _metric_label(category)
    disp = _metric_display(category, metric_num)
    my_f = float(metric_num) if isinstance(metric_num, Decimal) else float(metric_num or 0)
    mv = my_f

    th_f: float | None = None
    if threshold is not None:
        th_f = float(threshold) if isinstance(threshold, Decimal) else float(threshold)

    prog = _progress_pct(rank, my_f, th_f)
    msg = _next_target_message(category, rank, metric_num, threshold)

    if in_ranking and rank is not None and rank <= TOP_N:
        prog = 100

    return {
        "authenticated": True,
        "category": category,
        "rank": rank,
        "metric_label": ml,
        "metric_value": mv,
        "metric_display": disp,
        "next_target_label": msg,
        "progress_percent": prog,
        "in_ranking": in_ranking,
    }
