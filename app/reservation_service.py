"""Finalização de reservas de bilhetes (carteira, MP, manual)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Raffle, RaffleStatus, Ticket, Transaction, User

# Reservas pending_payment sem pagamento são libertadas após este tempo (minutos).
RESERVATION_TTL_MINUTES = 15


async def load_pending_tickets_for_hold(
    session: AsyncSession,
    hold_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> list[Ticket]:
    q = select(Ticket).where(
        Ticket.payment_hold_id == hold_id,
        Ticket.status == "pending_payment",
    )
    if user_id is not None:
        q = q.where(Ticket.user_id == user_id)
    r = await session.execute(q.order_by(Ticket.ticket_number))
    return list(r.scalars().all())


async def finalize_hold_as_paid(
    session: AsyncSession,
    hold_id: uuid.UUID,
    *,
    mark_raffle_payment_tx_id: uuid.UUID | None = None,
) -> tuple[list[Ticket], Raffle | None]:
    """
    Marca todos os bilhetes do hold como paid, cria transactions type=purchase,
    opcionalmente marca Transaction raffle_payment como completed.
    """
    tickets = await load_pending_tickets_for_hold(session, hold_id)
    if not tickets:
        return [], None

    r_result = await session.execute(
        select(Raffle).where(Raffle.id == tickets[0].raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        return [], None

    price = raffle.ticket_price
    for t in tickets:
        t.status = "paid"
        session.add(
            Transaction(
                user_id=t.user_id,
                amount=price,
                type="purchase",
                status="completed",
                description=f"Compra bilhete nº {t.ticket_number} — {raffle.title}",
            ),
        )

    if mark_raffle_payment_tx_id is not None:
        tx_r = await session.execute(
            select(Transaction).where(Transaction.id == mark_raffle_payment_tx_id).with_for_update(),
        )
        rtx = tx_r.scalar_one_or_none()
        if rtx is not None:
            rtx.status = "completed"

    sold = await session.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.raffle_id == raffle.id,
            Ticket.status == "paid",
        ),
    )
    if sold is not None and sold >= raffle.total_tickets:
        raffle.status = RaffleStatus.sold_out.value

    return tickets, raffle


async def delete_pending_tickets_for_hold(session: AsyncSession, hold_id: uuid.UUID) -> int:
    """Só remove bilhetes pending (liberta números). Mantém linhas de transação."""
    r = await session.execute(
        delete(Ticket).where(
            Ticket.payment_hold_id == hold_id,
            Ticket.status == "pending_payment",
        ),
    )
    return int(r.rowcount or 0)


async def cancel_hold_reservation(session: AsyncSession, hold_id: uuid.UUID) -> int:
    """Admin / usuário: remove bilhetes pending e transação raffle_payment pendente."""
    n = await delete_pending_tickets_for_hold(session, hold_id)
    await session.execute(
        delete(Transaction).where(
            Transaction.payment_hold_id == hold_id,
            Transaction.type == "raffle_payment",
            Transaction.status == "pending",
        ),
    )
    return n


async def expire_stale_pending_reservations(
    session: AsyncSession,
    *,
    raffle_id: uuid.UUID | None = None,
) -> int:
    """
    Remove bilhetes pending_payment mais antigos que RESERVATION_TTL_MINUTES
    e a transação raffle_payment pendente do mesmo hold.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RESERVATION_TTL_MINUTES)
    q = (
        select(Ticket.payment_hold_id)
        .where(
            Ticket.status == "pending_payment",
            Ticket.payment_hold_id.isnot(None),
            Ticket.created_at < cutoff,
        )
        .distinct()
    )
    if raffle_id is not None:
        q = q.where(Ticket.raffle_id == raffle_id)
    r = await session.execute(q)
    hold_ids = [row[0] for row in r.fetchall()]
    total = 0
    for hid in hold_ids:
        if hid is not None:
            total += await cancel_hold_reservation(session, hid)
    return total
