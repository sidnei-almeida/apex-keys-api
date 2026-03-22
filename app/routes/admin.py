from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Raffle, RaffleStatus, Ticket, Transaction, User
from app.pricing import tactical_ticket_price
from app.schemas import (
    AdminRaffleCreate,
    AdminWalletAdjust,
    AdminWalletAdjustResponse,
    RaffleCancelResponse,
    RafflePublic,
)
from app.security import get_current_admin

router = APIRouter()


@router.post("/users/{user_id}/adjust-balance", response_model=AdminWalletAdjustResponse)
async def adjust_user_balance(
    user_id: UUID,
    body: AdminWalletAdjust,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminWalletAdjustResponse:
    u_result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = u_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    previous = user.balance
    new_balance = previous + body.amount
    if new_balance < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Saldo ficaria negativo: {previous} + {body.amount} = {new_balance}",
        )
    user.balance = new_balance
    session.add(
        Transaction(
            user_id=user_id,
            amount=body.amount,
            type="admin_adjustment",
            status="completed",
            description=body.description or f"Ajuste manual: {body.amount}",
        ),
    )
    return AdminWalletAdjustResponse(
        user_id=user_id,
        previous_balance=previous,
        new_balance=new_balance,
        amount_adjusted=body.amount,
    )


@router.post("/raffles", response_model=RafflePublic, status_code=status.HTTP_201_CREATED)
async def create_raffle(
    body: AdminRaffleCreate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RafflePublic:
    ticket_price = tactical_ticket_price(body.total_price, body.total_tickets)
    raffle = Raffle(
        title=body.title,
        image_url=body.image_url,
        video_id=body.video_id,
        total_price=body.total_price,
        total_tickets=body.total_tickets,
        ticket_price=ticket_price,
        status=RaffleStatus.active.value,
    )
    session.add(raffle)
    await session.flush()
    await session.refresh(raffle)
    return RafflePublic.model_validate(raffle)


@router.post("/raffles/{raffle_id}/cancel", response_model=RaffleCancelResponse)
async def cancel_raffle(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RaffleCancelResponse:
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status != RaffleStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas sorteios ativos podem ser cancelados",
        )

    raffle.status = RaffleStatus.canceled.value

    t_result = await session.execute(
        select(Ticket)
        .where(Ticket.raffle_id == raffle_id, Ticket.status == "paid")
        .order_by(Ticket.user_id, Ticket.ticket_number)
        .with_for_update(),
    )
    tickets = t_result.scalars().all()

    refunds = 0
    price = raffle.ticket_price
    for t in tickets:
        u_result = await session.execute(select(User).where(User.id == t.user_id).with_for_update())
        user = u_result.scalar_one_or_none()
        if user is None:
            continue
        user.balance = user.balance + price
        session.add(
            Transaction(
                user_id=user.id,
                amount=price,
                type="refund",
                status="completed",
                description=f"Estorno — rifa cancelada ({raffle.title}) — bilhete nº {t.ticket_number}",
            ),
        )
        refunds += 1

    return RaffleCancelResponse(raffle_id=raffle_id, status="canceled", refunds_issued=refunds)
