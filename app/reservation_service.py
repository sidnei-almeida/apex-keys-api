"""Finalização de reservas de bilhetes (carteira, MP, manual)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Raffle, RaffleStatus, Ticket, Transaction, User

# Reservas pending_payment sem pagamento são libertadas após este tempo (minutos).
RESERVATION_TTL_MINUTES = 15

# Registos `raffle_payment` terminal (pago / cancelado / falha) são apagados da BD após este período (dias).
RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS = 14


def reservation_expires_at_utc(created_min: datetime) -> datetime:
    """Fim da janela de pagamento (15 min) a partir do `created_at` mais antigo do hold."""
    if created_min.tzinfo is None:
        created_min = created_min.replace(tzinfo=timezone.utc)
    return created_min + timedelta(minutes=RESERVATION_TTL_MINUTES)


async def purge_stale_raffle_payment_audit_records(session: AsyncSession) -> int:
    """
    Remove transações `raffle_payment` já finalizadas com mais de
    RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS (retenção mínima para disputas).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS)
    r = await session.execute(
        delete(Transaction).where(
            Transaction.type == "raffle_payment",
            Transaction.status.in_(("canceled", "failed", "completed")),
            Transaction.created_at < cutoff,
        ),
    )
    return int(r.rowcount or 0)


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
        became_sold_out = raffle.status == RaffleStatus.active.value
        raffle.status = RaffleStatus.sold_out.value
        if became_sold_out:
            from app.live_draw_service import schedule_live_draw_if_needed

            await schedule_live_draw_if_needed(session, raffle)

    return tickets, raffle


async def delete_pending_tickets_for_hold(session: AsyncSession, hold_id: uuid.UUID) -> int:
    """Só remove bilhetes pending (liberta números)."""
    r = await session.execute(
        delete(Ticket).where(
            Ticket.payment_hold_id == hold_id,
            Ticket.status == "pending_payment",
        ),
    )
    return int(r.rowcount or 0)


def _snapshot_from_tickets(tickets: list[Ticket], raffle: Raffle | None) -> dict:
    nums = sorted(t.ticket_number for t in tickets)
    rid = str(tickets[0].raffle_id)
    title = raffle.title if raffle is not None else ""
    return {
        "raffle_id": rid,
        "raffle_title": title,
        "ticket_numbers": nums,
    }


async def ensure_raffle_checkout_snapshot(
    session: AsyncSession,
    hold_id: uuid.UUID,
    tickets: list[Ticket],
) -> None:
    """Garante JSON de auditoria nas transações raffle_payment pending do hold."""
    if not tickets:
        return
    r_result = await session.execute(select(Raffle).where(Raffle.id == tickets[0].raffle_id))
    raffle = r_result.scalar_one_or_none()
    snap = _snapshot_from_tickets(tickets, raffle)
    await session.execute(
        update(Transaction)
        .where(
            Transaction.payment_hold_id == hold_id,
            Transaction.type == "raffle_payment",
            Transaction.status == "pending",
        )
        .values(raffle_checkout_snapshot=snap),
    )


async def mark_raffle_payment_tx_end(
    session: AsyncSession,
    hold_id: uuid.UUID,
    *,
    new_status: str,
    audit_suffix: str,
) -> None:
    """pending → canceled ou failed; mantém linha para disputas/auditoria."""
    tx_r = await session.execute(
        select(Transaction).where(
            Transaction.payment_hold_id == hold_id,
            Transaction.type == "raffle_payment",
            Transaction.status == "pending",
        ),
    )
    for tx in tx_r.scalars().all():
        tx.status = new_status
        extra = f" | {audit_suffix}"
        base = (tx.description or "")[: max(0, 500 - len(extra))]
        tx.description = base + extra


async def cancel_hold_reservation(
    session: AsyncSession,
    hold_id: uuid.UUID,
    *,
    reason: str = "reserva_cancelada",
) -> int:
    """
    Liberta números (apaga bilhetes pending).
    Transação raffle_payment pending → status canceled (não apaga o registo).
    """
    pending = await load_pending_tickets_for_hold(session, hold_id)
    if pending:
        await ensure_raffle_checkout_snapshot(session, hold_id, pending)
    await mark_raffle_payment_tx_end(
        session,
        hold_id,
        new_status="canceled",
        audit_suffix=f"[Cancelado: {reason}]",
    )
    n = await delete_pending_tickets_for_hold(session, hold_id)
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
            total += await cancel_hold_reservation(
                session,
                hid,
                reason="expiracao_automatica_15min",
            )
    return total
