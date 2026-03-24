"""Reserva atômica de números + pagamento (carteira ou Pix Mercado Pago)."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.mercado_pago_service import MercadoPagoApiError, create_pix_payment, extract_pix_qr_from_payment
from app.models import Raffle, RaffleStatus, Ticket, Transaction, User
from app.reservation_service import (
    cancel_hold_reservation,
    expire_stale_pending_reservations,
    finalize_hold_as_paid,
    load_pending_tickets_for_hold,
)
from app.schemas import (
    CompleteReservationWalletBody,
    ReservationPixIntentBody,
    ReservationStatusOut,
    ReserveRaffleTicketsBody,
    ReserveRaffleTicketsResponse,
)
from app.security import get_current_user_id
from app.utils import mock_pix_qr_payload

router = APIRouter()
logger = logging.getLogger("apex_keys.raffle_reservations")


@router.post("/checkout/reserve-tickets", response_model=ReserveRaffleTicketsResponse)
async def reserve_raffle_tickets(
    body: ReserveRaffleTicketsBody,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ReserveRaffleTicketsResponse:
    nums = sorted({int(n) for n in body.ticket_numbers})
    if len(nums) != len(body.ticket_numbers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lista de números contém duplicados",
        )

    await expire_stale_pending_reservations(session, raffle_id=body.raffle_id)

    r_result = await session.execute(
        select(Raffle).where(Raffle.id == body.raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status != RaffleStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sorteio não está ativo para reservas",
        )

    for n in nums:
        if n < 1 or n > raffle.total_tickets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Número {n} fora do intervalo do sorteio",
            )
        taken = await session.execute(
            select(Ticket.id).where(
                Ticket.raffle_id == body.raffle_id,
                Ticket.ticket_number == n,
            ),
        )
        if taken.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Número já foi reservado ou vendido",
            )

    hold_id = uuid.uuid4()
    price = raffle.ticket_price
    try:
        for n in nums:
            session.add(
                Ticket(
                    raffle_id=body.raffle_id,
                    user_id=user_id,
                    ticket_number=n,
                    status="pending_payment",
                    payment_hold_id=hold_id,
                ),
            )
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concorrência: um dos números acabou de ser reservado",
        ) from None

    total = price * len(nums)
    await session.commit()
    logger.info(
        "[reserve] hold=%s user=%s raffle=%s numbers=%s total=%s",
        hold_id,
        user_id,
        body.raffle_id,
        nums,
        total,
    )
    return ReserveRaffleTicketsResponse(
        payment_hold_id=hold_id,
        raffle_id=body.raffle_id,
        ticket_numbers=nums,
        total_amount=total,
    )


async def _pending_raffle_tx(session: AsyncSession, hold_id: UUID) -> Transaction | None:
    r = await session.execute(
        select(Transaction).where(
            Transaction.payment_hold_id == hold_id,
            Transaction.type == "raffle_payment",
        ),
    )
    return r.scalar_one_or_none()


@router.post("/checkout/complete-reservation-wallet")
async def complete_reservation_wallet(
    body: CompleteReservationWalletBody,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    hold_id = body.payment_hold_id
    pending_tx = await _pending_raffle_tx(session, hold_id)
    if pending_tx is not None and pending_tx.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe cobrança Pix pendente para esta reserva; cancele ou pague o Pix",
        )

    tickets = await load_pending_tickets_for_hold(session, hold_id, user_id=user_id)
    if not tickets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reserva não encontrada ou já finalizada",
        )

    r_result = await session.execute(
        select(Raffle).where(Raffle.id == tickets[0].raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None or raffle.status != RaffleStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sorteio não está mais ativo",
        )

    u_result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = u_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    total = raffle.ticket_price * len(tickets)
    if user.balance < total:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo insuficiente na carteira",
        )

    user.balance = user.balance - total
    await finalize_hold_as_paid(session, hold_id, mark_raffle_payment_tx_id=None)
    await session.commit()
    await session.refresh(user)
    logger.info(
        "[reserve_wallet] hold=%s user=%s paid=%s new_balance=%s",
        hold_id,
        user_id,
        total,
        user.balance,
    )
    return {
        "ok": True,
        "payment_hold_id": str(hold_id),
        "amount_charged": str(total),
        "new_balance": str(user.balance),
    }


@router.post("/checkout/reservation-pix-intent")
async def create_reservation_pix_intent(
    body: ReservationPixIntentBody,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    hold_id = body.payment_hold_id
    tickets = await load_pending_tickets_for_hold(session, hold_id, user_id=user_id)
    if not tickets:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reserva não encontrada ou já finalizada",
        )

    r_result = await session.execute(select(Raffle).where(Raffle.id == tickets[0].raffle_id))
    raffle = r_result.scalar_one_or_none()
    if raffle is None or raffle.status != RaffleStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sorteio não está mais ativo",
        )

    existing = await _pending_raffle_tx(session, hold_id)
    if existing is not None:
        if existing.status == "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe cobrança Pix pendente para esta reserva",
            )
        if existing.status == "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pagamento desta reserva já foi concluído",
            )

    dup = await session.execute(
        select(Transaction.id).where(Transaction.gateway_reference == body.gateway_reference),
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="gateway_reference já utilizado",
        )

    total = raffle.ticket_price * len(tickets)
    nums_preview = ", ".join(str(t.ticket_number) for t in tickets[:12])
    if len(tickets) > 12:
        nums_preview += "…"
    desc = f"Rifa: {raffle.title} — números {nums_preview}"

    settings = get_settings()
    mp_token = (settings.mercado_pago_access_token or "").strip()

    u_result = await session.execute(select(User).where(User.id == user_id))
    user = u_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    tr = Transaction(
        user_id=user_id,
        amount=total,
        type="raffle_payment",
        status="pending",
        gateway_reference=body.gateway_reference,
        description=desc[:500],
        payment_hold_id=hold_id,
    )
    session.add(tr)
    await session.flush()

    if mp_token:
        try:
            payment = await create_pix_payment(
                mp_token,
                amount=total,
                payer_email=user.email,
                external_reference=body.gateway_reference,
                description=desc,
            )
        except MercadoPagoApiError as e:
            await session.rollback()
            logger.error(
                "[reserve_pix] MP error user=%s hold=%s ref=%s http=%s",
                user_id,
                hold_id,
                body.gateway_reference[:48],
                e.status_code,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=e.args[0],
            ) from e

        tr.description = f"{desc[:400]} (MP id={payment.get('id')})"[:500]
        await session.commit()
        mp_payload = extract_pix_qr_from_payment(payment)
        return {
            "message": "Cobrança Pix criada (Mercado Pago)",
            "provider": "mercadopago",
            "mercado_pago": mp_payload,
            "mock_pix": None,
            "transaction_id": str(tr.id),
            "payment_hold_id": str(hold_id),
            "amount": str(total),
        }

    await session.commit()
    qr = mock_pix_qr_payload(body.gateway_reference, total)
    return {
        "message": "Cobrança Pix mock (sem token MP)",
        "provider": "mock",
        "mercado_pago": None,
        "mock_pix": qr,
        "transaction_id": str(tr.id),
        "payment_hold_id": str(hold_id),
        "amount": str(total),
    }


@router.get("/checkout/reservation/{hold_id}/status", response_model=ReservationStatusOut)
async def reservation_status(
    hold_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> ReservationStatusOut:
    paid_any = await session.execute(
        select(Ticket.ticket_number).where(
            Ticket.payment_hold_id == hold_id,
            Ticket.user_id == user_id,
            Ticket.status == "paid",
        ),
    )
    paid_nums = [int(x[0]) for x in paid_any.fetchall()]
    if paid_nums:
        r0 = await session.execute(
            select(Ticket.raffle_id).where(
                Ticket.payment_hold_id == hold_id,
                Ticket.user_id == user_id,
            ).limit(1),
        )
        rid = r0.scalar_one_or_none()
        tx_row = await _pending_raffle_tx(session, hold_id)
        return ReservationStatusOut(
            payment_hold_id=hold_id,
            state="paid",
            raffle_id=rid,
            ticket_numbers=sorted(paid_nums),
            transaction_status=tx_row.status if tx_row else None,
            gateway_reference=tx_row.gateway_reference if tx_row else None,
        )

    pending = await load_pending_tickets_for_hold(session, hold_id, user_id=user_id)
    if pending:
        tx_row = await _pending_raffle_tx(session, hold_id)
        return ReservationStatusOut(
            payment_hold_id=hold_id,
            state="pending_payment",
            raffle_id=pending[0].raffle_id,
            ticket_numbers=sorted(t.ticket_number for t in pending),
            transaction_status=tx_row.status if tx_row else None,
            gateway_reference=tx_row.gateway_reference if tx_row else None,
        )

    return ReservationStatusOut(payment_hold_id=hold_id, state="unknown")


@router.post("/checkout/reservation/{hold_id}/release")
async def release_reservation_self(
    hold_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Liberta números pending deste utilizador (desistência).
    Remove também cobrança Pix local pendente; o QR deixa de corresponder a uma reserva ativa.
    """
    pending = await load_pending_tickets_for_hold(session, hold_id, user_id=user_id)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nada para liberar",
        )
    n = await cancel_hold_reservation(session, hold_id)
    await session.commit()
    logger.info(
        "[release] user=%s hold=%s released_tickets=%s",
        user_id,
        hold_id,
        n,
    )
    return {"released": n}


def amounts_match_mp(db_amount: Decimal, mp_amount: Decimal) -> bool:
    return db_amount.quantize(Decimal("0.01")) == mp_amount.quantize(Decimal("0.01"))
