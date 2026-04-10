"""
Sorteio ao vivo: ao esgotar 100% dos bilhetes **pagos**, agenda +5 min e notifica compradores.
Após o horário, o primeiro pedido público idempotente executa o sorteio aleatório (secrets).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brasil_time import format_brasilia_hm
from app.models import Notification, Raffle, RaffleStatus, Ticket, User

LIVE_DRAW_DELAY = timedelta(minutes=5)


async def schedule_live_draw_if_needed(session: AsyncSession, raffle: Raffle) -> None:
    """
    Chamado quando `raffle.status` acabou de passar a `sold_out`.
    Define `scheduled_live_draw_at` uma única vez e notifica todos os compradores (distinct user_id).
    """
    if raffle.status != RaffleStatus.sold_out.value:
        return
    if raffle.scheduled_live_draw_at is not None:
        return
    now = datetime.now(timezone.utc)
    raffle.scheduled_live_draw_at = now + LIVE_DRAW_DELAY
    await _notify_buyers_live_draw_scheduled(session, raffle.id, raffle.title, raffle.scheduled_live_draw_at)


async def _notify_buyers_live_draw_scheduled(
    session: AsyncSession,
    raffle_id: UUID,
    raffle_title: str,
    at_utc: datetime,
) -> None:
    uids_r = await session.execute(
        select(Ticket.user_id)
        .where(Ticket.raffle_id == raffle_id, Ticket.status == "paid")
        .distinct(),
    )
    user_ids = [row[0] for row in uids_r.all()]
    # Horário de Brasília (não usar astimezone() sem TZ: no servidor EUA ficava errado)
    br_hm = format_brasilia_hm(at_utc)
    title = f"Sorteio ao vivo: {raffle_title}"
    body = (
        f"Todos os números foram vendidos! Em 5 minutos ({br_hm}, horário de Brasília) "
        f"corre o sorteio na roleta ao vivo. Entra em /raffle/{raffle_id}/sorteio para acompanhar."
    )
    for uid in user_ids:
        session.add(
            Notification(
                user_id=uid,
                type="raffle_live_draw_soon",
                title=title[:255],
                body=body,
            ),
        )


async def execute_random_draw_for_sold_out_raffle(session: AsyncSession, raffle: Raffle) -> tuple[int, UUID, str]:
    """
    Rifa com lock `with_for_update`: status sold_out, sem vencedor.
    Escolhe um bilhete pago ao acaso, grava vencedor, status finished.
    """
    if raffle.status != RaffleStatus.sold_out.value:
        raise ValueError("rifa não está sold_out")
    if raffle.winning_ticket_number is not None:
        raise ValueError("rifa já tem vencedor")

    tr = await session.execute(
        select(Ticket, User)
        .join(User, Ticket.user_id == User.id)
        .where(Ticket.raffle_id == raffle.id, Ticket.status == "paid"),
    )
    rows: list[tuple[Ticket, User]] = list(tr.all())
    if not rows:
        raise ValueError("sem bilhetes pagos")

    winning_ticket, winning_user = secrets.SystemRandom().choice(rows)
    n = winning_ticket.ticket_number
    raffle.winning_ticket_number = n
    raffle.drawn_at = datetime.now(timezone.utc)
    raffle.status = RaffleStatus.finished.value
    await session.flush()
    await notify_winner_steam_redemption_if_set(session, raffle, winning_user.id)
    await session.refresh(raffle)
    return n, winning_user.id, winning_user.full_name


async def notify_winner_steam_redemption_if_set(
    session: AsyncSession,
    raffle: Raffle,
    winner_user_id: UUID,
) -> None:
    """Se existir código Steam na rifa, notifica só o vencedor (in-app)."""
    code = (raffle.steam_redemption_code or "").strip()
    if not code:
        return
    title = f"Ganhaste: {raffle.title}"
    body = (
        f"O teu código Steam para resgate: {code}\n"
        "Guarda-o num local seguro. Em caso de dúvida, contacta o suporte."
    )
    session.add(
        Notification(
            user_id=winner_user_id,
            type="raffle_winner_steam",
            title=title[:255],
            body=body,
        ),
    )
    await session.flush()


async def run_scheduled_live_draw_if_due(session: AsyncSession, raffle_id: UUID) -> bool:
    """
    Se sold_out, tem `scheduled_live_draw_at` no passado e ainda sem vencedor, executa o sorteio.
    Retorna True se executou sorteio nesta chamada.
    """
    r_result = await session.execute(select(Raffle).where(Raffle.id == raffle_id).with_for_update())
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        return False
    if raffle.status != RaffleStatus.sold_out.value:
        return False
    if raffle.winning_ticket_number is not None:
        return False
    if raffle.scheduled_live_draw_at is None:
        return False
    now = datetime.now(timezone.utc)
    sched = raffle.scheduled_live_draw_at
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=timezone.utc)
    if now < sched:
        return False
    await execute_random_draw_for_sold_out_raffle(session, raffle)
    return True
