from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Raffle, RaffleStatus, Ticket, Transaction, User
from app.schemas import RafflePublic, TicketPurchaseRequest, TicketPurchaseResponse
from app.security import get_current_user_id

router = APIRouter()

_VALID_RAFFLE_STATUS = frozenset(s.value for s in RaffleStatus)


@router.get("/raffles", response_model=list[RafflePublic])
async def list_raffles(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="active | sold_out | finished | canceled",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[RafflePublic]:
    if status_filter is None:
        result = await session.execute(
            select(Raffle).order_by(Raffle.created_at.desc()).limit(100),
        )
    else:
        s = status_filter.lower().strip()
        if s not in _VALID_RAFFLE_STATUS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status deve ser active, sold_out, finished ou canceled",
            )
        result = await session.execute(
            select(Raffle).where(Raffle.status == s).order_by(Raffle.created_at.desc()).limit(100),
        )
    rows = result.scalars().all()
    return [RafflePublic.model_validate(r) for r in rows]


@router.post("/buy-ticket", response_model=TicketPurchaseResponse)
async def buy_ticket(
    body: TicketPurchaseRequest,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> TicketPurchaseResponse:
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == body.raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status != RaffleStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sorteio não está ativo para compras",
        )
    if body.ticket_number > raffle.total_tickets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Número fora do intervalo do sorteio",
        )

    taken = await session.execute(
        select(Ticket.id).where(
            Ticket.raffle_id == body.raffle_id,
            Ticket.ticket_number == body.ticket_number,
        ),
    )
    if taken.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este número já foi vendido",
        )

    u_result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = u_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    price = raffle.ticket_price
    if user.balance < price:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo insuficiente na carteira",
        )

    user.balance = user.balance - price

    try:
        ticket = Ticket(
            raffle_id=body.raffle_id,
            user_id=user_id,
            ticket_number=body.ticket_number,
            status="paid",
        )
        session.add(ticket)
        await session.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concorrência: número acabou de ser vendido",
        ) from None

    session.add(
        Transaction(
            user_id=user_id,
            amount=price,
            type="purchase",
            status="completed",
            description=f"Compra bilhete nº {body.ticket_number} — {raffle.title}",
        ),
    )

    sold = await session.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.raffle_id == raffle.id,
            Ticket.status == "paid",
        ),
    )
    if sold is not None and sold >= raffle.total_tickets:
        raffle.status = RaffleStatus.sold_out.value

    await session.refresh(user)
    return TicketPurchaseResponse(
        ticket_id=ticket.id,
        raffle_id=body.raffle_id,
        ticket_number=body.ticket_number,
        amount_charged=price,
        new_balance=user.balance,
    )
