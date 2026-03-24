import asyncio
import re
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.email_service import send_raffle_canceled_refund_email
from app.models import Notification, Raffle, RaffleStatus, Ticket, Transaction, User
from app.pricing import tactical_ticket_price
from app.schemas import (
    AdminRaffleCreate,
    AdminWalletAdjust,
    AdminWalletAdjustResponse,
    RaffleCancelResponse,
    RaffleDeleteResponse,
    RaffleImagePatch,
    RafflePublic,
    RaffleUpdate,
    RaffleVideoPatch,
)
from app.security import get_current_admin

router = APIRouter()

_YOUTUBE_ID_RE = re.compile(r"(?:[?&]v=|/embed/|youtu\.be/)([a-zA-Z0-9_-]{11})\b")


def _youtube_id_from_url(url: str | None) -> str | None:
    """Extrai o video_id (11 chars) de URL YouTube ou devolve o próprio valor se já for ID."""
    if not url or not url.strip():
        return None
    u = url.strip()
    m = _YOUTUBE_ID_RE.search(u)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", u):
        return u
    return None


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


@router.get("/raffles/{raffle_id}", response_model=RafflePublic)
async def get_raffle_admin(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RafflePublic:
    result = await session.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    return RafflePublic.model_validate(raffle)


@router.put("/raffles/{raffle_id}", response_model=RafflePublic)
async def update_raffle(
    raffle_id: UUID,
    body: RaffleUpdate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RafflePublic:
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")

    data = body.model_dump(exclude_unset=True)
    if not data:
        return RafflePublic.model_validate(raffle)

    need_recalc = "total_price" in data or "total_tickets" in data
    if need_recalc:
        if raffle.status == RaffleStatus.canceled.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não é possível alterar preço ou quantidade de bilhetes num sorteio cancelado",
            )
        new_total_price = data["total_price"] if "total_price" in data else raffle.total_price
        new_total_tickets = data["total_tickets"] if "total_tickets" in data else raffle.total_tickets

        max_num_result = await session.execute(
            select(func.max(Ticket.ticket_number)).where(
                Ticket.raffle_id == raffle.id,
                Ticket.status == "paid",
            ),
        )
        max_sold_number = max_num_result.scalar()
        min_tickets = int(max_sold_number or 0)
        if new_total_tickets < min_tickets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"total_tickets não pode ser inferior a {min_tickets} "
                    "(já existem bilhetes pagos até esse número)"
                ),
            )

        raffle.total_price = new_total_price
        raffle.total_tickets = new_total_tickets
        raffle.ticket_price = tactical_ticket_price(new_total_price, new_total_tickets)

    for key in ("title", "image_url", "video_id"):
        if key in data:
            setattr(raffle, key, data[key])

    await session.flush()
    await session.refresh(raffle)
    return RafflePublic.model_validate(raffle)


@router.patch("/raffles/{raffle_id}/image", response_model=RafflePublic)
async def patch_raffle_image(
    raffle_id: UUID,
    body: RaffleImagePatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RafflePublic:
    """Atualiza só o campo image_url da rifa (URL da capa em 1080p)."""
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    raffle.image_url = body.image_url
    await session.flush()
    await session.refresh(raffle)
    return RafflePublic.model_validate(raffle)


@router.patch("/raffles/{raffle_id}/video", response_model=RafflePublic)
async def patch_raffle_video(
    raffle_id: UUID,
    body: RaffleVideoPatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RafflePublic:
    """
    Atualiza só o campo video_id da rifa.
    Aceita URL completa (watch?v=, youtu.be/, embed/) ou só o ID; grava o video_id.
    """
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if body.youtube_url is None:
        raffle.video_id = None
    else:
        vid = _youtube_id_from_url(body.youtube_url)
        if vid is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL ou ID do YouTube inválido. Use watch?v=, youtu.be/ ou embed/.",
            )
        raffle.video_id = vid
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
    user_ticket_count: dict[UUID, int] = defaultdict(int)

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
        user_ticket_count[t.user_id] += 1
        refunds += 1

    # Notificações in-app e emails para cada usuário afetado
    if user_ticket_count:
        user_ids = list(user_ticket_count.keys())
        users_result = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u for u in users_result.scalars().all()}

        for uid, count in user_ticket_count.items():
            user = users_by_id.get(uid)
            if user is None:
                continue
            total_refund = price * count
            amount_str = f"R$ {float(total_refund):.2f}".replace(".", ",")
            title = f"Rifa cancelada: {raffle.title}"
            body = f"O valor de {amount_str} foi creditado na sua carteira. Você pode utilizar em outras rifas ou solicitar saque."

            session.add(
                Notification(
                    user_id=uid,
                    type="raffle_canceled_refund",
                    title=title,
                    body=body,
                ),
            )
            # Enviar email em background (não bloqueia a resposta)
            asyncio.create_task(
                send_raffle_canceled_refund_email(
                    to_email=user.email,
                    full_name=user.full_name,
                    raffle_title=raffle.title,
                    amount_refunded=amount_str,
                ),
            )

    return RaffleCancelResponse(raffle_id=raffle_id, status="canceled", refunds_issued=refunds)


@router.delete("/raffles/{raffle_id}", response_model=RaffleDeleteResponse)
async def delete_raffle(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> RaffleDeleteResponse:
    """
    Remove a rifa e todos os bilhetes (`tickets`) ligados a ela.

    Se ainda existirem bilhetes pagos e a rifa **não** estiver cancelada, responde 409:
    é necessário cancelar antes (`POST .../cancel`) para estornar os compradores.
    Transações (`transactions`) na carteira mantêm-se como histórico (sem FK para a rifa).
    """
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")

    if raffle.status != RaffleStatus.canceled.value:
        paid_count_result = await session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.raffle_id == raffle_id, Ticket.status == "paid"),
        )
        if int(paid_count_result.scalar_one() or 0) > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Esta rifa tem bilhetes pagos. Cancele primeiro com "
                    "POST /api/v1/admin/raffles/{id}/cancel para estornar os compradores; "
                    "depois pode apagar."
                ),
            )

    count_result = await session.execute(
        select(func.count()).select_from(Ticket).where(Ticket.raffle_id == raffle_id),
    )
    tickets_removed = int(count_result.scalar_one() or 0)

    await session.execute(delete(Ticket).where(Ticket.raffle_id == raffle_id))
    await session.execute(delete(Raffle).where(Raffle.id == raffle_id))
    await session.flush()

    return RaffleDeleteResponse(raffle_id=raffle_id, tickets_removed=tickets_removed)
