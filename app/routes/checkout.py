from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, asc, case, desc, func, nulls_last, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.live_draw_service import run_scheduled_live_draw_if_due, schedule_live_draw_if_needed
from app.models import Raffle, RaffleStatus, Ticket, Transaction, User
from app.reservation_service import expire_stale_pending_reservations
from app.schemas import (
    HallOfFameEntryOut,
    HallOfFameSpotlightRaffle,
    PublicLiveDrawOut,
    PublicWheelSegmentOut,
    RaffleDetailOut,
    RaffleListOut,
    RafflePublic,
    RecentPurchasePulseOut,
    TicketPurchaseRequest,
    TicketPurchaseResponse,
)
from app.security import get_current_user_id

router = APIRouter()

_VALID_RAFFLE_STATUS = frozenset(s.value for s in RaffleStatus)


async def _build_public_live_draw(session: AsyncSession, raffle: Raffle) -> PublicLiveDrawOut:
    now = datetime.now(timezone.utc)
    st: str = raffle.status
    if st not in ("active", "sold_out", "finished", "canceled"):
        st = "active"

    seconds_until: int | None = None
    sched = raffle.scheduled_live_draw_at
    if (
        st == RaffleStatus.sold_out.value
        and sched is not None
        and raffle.winning_ticket_number is None
    ):
        s_utc = sched if sched.tzinfo else sched.replace(tzinfo=timezone.utc)
        seconds_until = int(max(0, (s_utc - now).total_seconds()))

    segments: list[PublicWheelSegmentOut] = []
    if st in (RaffleStatus.sold_out.value, RaffleStatus.finished.value):
        tr = await session.execute(
            select(Ticket.ticket_number, User.full_name)
            .join(User, Ticket.user_id == User.id)
            .where(Ticket.raffle_id == raffle.id, Ticket.status == "paid")
            .order_by(Ticket.ticket_number.asc()),
        )
        segments = [
            PublicWheelSegmentOut(ticket_number=int(row[0]), full_name=str(row[1])) for row in tr.all()
        ]

    winner_name: str | None = None
    if raffle.winning_ticket_number is not None and st == RaffleStatus.finished.value:
        wnr = await session.execute(
            select(User.full_name)
            .select_from(Ticket)
            .join(User, Ticket.user_id == User.id)
            .where(
                Ticket.raffle_id == raffle.id,
                Ticket.ticket_number == raffle.winning_ticket_number,
                Ticket.status == "paid",
            )
            .limit(1),
        )
        winner_name = wnr.scalar_one_or_none()

    return PublicLiveDrawOut(
        raffle_id=raffle.id,
        raffle_title=raffle.title,
        status=st,
        server_now=now,
        scheduled_live_draw_at=raffle.scheduled_live_draw_at,
        seconds_until_draw=seconds_until,
        winner_ticket_number=raffle.winning_ticket_number,
        winner_full_name=winner_name,
        segments=segments,
    )


def _mask_display_name_for_pulse(full_name: str) -> str:
    """Primeiro nome + inicial do último sobrenome (ex.: Maria S.)."""
    parts = full_name.strip().split()
    if not parts:
        return "Alguém"
    if len(parts) == 1:
        return parts[0][:80]
    first, last = parts[0], parts[-1]
    initial = (last[0].upper() + ".") if last else ""
    return f"{first} {initial}".strip()


def _raffles_public_list_order():
    """
    Ordem para a home e catálogos:
    - `featured` (ouro) primeiro — várias rifas podem ter este tier; entre elas,
      `created_at` ascendente (a mais antiga = posição 1 no hero / slideshow).
    - Depois `carousel` (prata), mais recentes primeiro.
    - Por último `none`, mais recentes primeiro.
    """
    tier_rank = case(
        (Raffle.featured_tier == "featured", 0),
        (Raffle.featured_tier == "carousel", 1),
        else_=2,
    )
    created_if_featured = case(
        (Raffle.featured_tier == "featured", Raffle.created_at),
    )
    created_if_not_featured = case(
        (Raffle.featured_tier != "featured", Raffle.created_at),
    )
    return (
        tier_rank,
        nulls_last(asc(created_if_featured)),
        nulls_last(desc(created_if_not_featured)),
    )


@router.get("/raffles/hall-of-fame", response_model=list[HallOfFameEntryOut])
async def hall_of_fame(
    session: AsyncSession = Depends(get_session),
) -> list[HallOfFameEntryOut]:
    """
    Top 5 utilizadores por número de rifas ganhas (rifas `finished` com bilhete vencedor pago).
    Inclui dados de destaque da vitória mais recente para os cartões do Hall.
    """
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
        .limit(5)
    )
    top_result = await session.execute(top_stmt)
    top_rows = top_result.all()
    if not top_rows:
        return []

    user_ids = [row[0] for row in top_rows]
    users_r = await session.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in users_r.scalars().all()}

    out: list[HallOfFameEntryOut] = []
    rank = 0
    for row in top_rows:
        uid, wins = row[0], int(row[1] or 0)
        user = users_by_id.get(uid)
        if user is None or wins < 1:
            continue
        spot_r = await session.execute(
            select(Raffle)
            .join(
                Ticket,
                and_(
                    Ticket.raffle_id == Raffle.id,
                    Ticket.user_id == uid,
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
        raffle = spot_r.scalar_one_or_none()
        if raffle is None or raffle.winning_ticket_number is None:
            continue
        rank += 1
        out.append(
            HallOfFameEntryOut(
                rank=rank,
                user_id=user.id,
                full_name=user.full_name,
                avatar_url=user.avatar_url,
                wins=wins,
                spotlight=HallOfFameSpotlightRaffle(
                    raffle_id=raffle.id,
                    title=raffle.title,
                    image_url=raffle.image_url,
                    winning_ticket_number=raffle.winning_ticket_number,
                ),
            ),
        )
    return out


@router.get("/recent-purchase-pulses", response_model=list[RecentPurchasePulseOut])
async def recent_purchase_pulses(
    limit: int = Query(24, ge=1, le=50, description="Máximo de eventos recentes"),
    session: AsyncSession = Depends(get_session),
) -> list[RecentPurchasePulseOut]:
    """
    Bilhetes pagos agrupados por utilizador, rifa e minuto (UTC), para prova social na home.
    O nome é parcial por privacidade.
    """
    minute_bucket = func.date_trunc("minute", Ticket.created_at)
    stmt = (
        select(
            Ticket.user_id,
            Ticket.raffle_id,
            minute_bucket.label("bucket"),
            func.count().label("qty"),
            func.max(Ticket.created_at).label("purchased_at"),
            User.full_name,
            Raffle.title,
        )
        .join(User, User.id == Ticket.user_id)
        .join(Raffle, Raffle.id == Ticket.raffle_id)
        .where(Ticket.status == "paid")
        .group_by(
            Ticket.user_id,
            Ticket.raffle_id,
            minute_bucket,
            User.full_name,
            Raffle.title,
        )
        .order_by(desc(func.max(Ticket.created_at)))
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.all()
    out: list[RecentPurchasePulseOut] = []
    for row in rows:
        uid, rid, bucket, qty, p_at, fname, rtitle = row
        q = int(qty or 0)
        if q < 1 or p_at is None:
            continue
        bucket_key = bucket.isoformat() if hasattr(bucket, "isoformat") else str(bucket)
        pulse_id = f"{uid}-{rid}-{bucket_key}"
        out.append(
            RecentPurchasePulseOut(
                id=pulse_id,
                display_name=_mask_display_name_for_pulse(fname or ""),
                quantity=q,
                raffle_title=(rtitle or "Sorteio")[:200],
                purchased_at=p_at,
            ),
        )
    return out


@router.get("/raffles", response_model=list[RaffleListOut])
async def list_raffles(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="active | sold_out | finished | canceled",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[RaffleListOut]:
    """
    Lista rifas. Ordem: todas com `featured_tier=featured` primeiro (várias
    permitidas; ver `_raffles_public_list_order`), depois carousel, depois none.
    """
    order = _raffles_public_list_order()
    if status_filter is None:
        result = await session.execute(select(Raffle).order_by(*order).limit(100))
    else:
        s = status_filter.lower().strip()
        if s not in _VALID_RAFFLE_STATUS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status deve ser active, sold_out, finished ou canceled",
            )
        result = await session.execute(
            select(Raffle).where(Raffle.status == s).order_by(*order).limit(100),
        )
    rows = result.scalars().all()
    out = []
    for r in rows:
        sold_result = await session.scalar(
            select(func.count()).select_from(Ticket).where(
                Ticket.raffle_id == r.id,
                Ticket.status == "paid",
            ),
        )
        sold = int(sold_result or 0)
        held_result = await session.scalar(
            select(func.count()).select_from(Ticket).where(
                Ticket.raffle_id == r.id,
                Ticket.status == "pending_payment",
            ),
        )
        held = int(held_result or 0)
        data = RafflePublic.model_validate(r).model_dump()
        out.append(RaffleListOut(**data, sold=sold, held=held))
    return out


@router.get("/raffles/{raffle_id}/live-draw", response_model=PublicLiveDrawOut)
async def get_raffle_live_draw(
    raffle_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> PublicLiveDrawOut:
    """
    Estado do sorteio ao vivo: countdown, segmentos da roleta (bilhetes pagos + nomes).
    Quando passa o horário agendado, executa o sorteio aleatório (idempotente) neste pedido.
    """
    await expire_stale_pending_reservations(session, raffle_id=raffle_id)
    await run_scheduled_live_draw_if_due(session, raffle_id)

    r_result = await session.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")

    return await _build_public_live_draw(session, raffle)


@router.get("/raffles/{raffle_id}", response_model=RaffleDetailOut)
async def get_raffle_detail(
    raffle_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> RaffleDetailOut:
    """Retorna detalhes da rifa (público), incluindo lista de números vendidos."""
    await expire_stale_pending_reservations(session, raffle_id=raffle_id)
    await run_scheduled_live_draw_if_due(session, raffle_id)

    r_result = await session.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")

    sold_result = await session.execute(
        select(Ticket.ticket_number).where(
            Ticket.raffle_id == raffle_id,
            Ticket.status == "paid",
        ),
    )
    sold_numbers = sorted(int(row[0]) for row in sold_result.fetchall())

    held_result = await session.execute(
        select(Ticket.ticket_number).where(
            Ticket.raffle_id == raffle_id,
            Ticket.status == "pending_payment",
        ),
    )
    held_numbers = sorted(int(row[0]) for row in held_result.fetchall())

    data = RafflePublic.model_validate(raffle).model_dump()
    return RaffleDetailOut(
        **data,
        sold=len(sold_numbers),
        held=len(held_numbers),
        sold_numbers=sold_numbers,
        held_numbers=held_numbers,
    )


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
        became_sold_out = raffle.status == RaffleStatus.active.value
        raffle.status = RaffleStatus.sold_out.value
        if became_sold_out:
            await schedule_live_draw_if_needed(session, raffle)

    await session.refresh(user)
    return TicketPurchaseResponse(
        ticket_id=ticket.id,
        raffle_id=body.raffle_id,
        ticket_number=body.ticket_number,
        amount_charged=price,
        new_balance=user.balance,
    )
