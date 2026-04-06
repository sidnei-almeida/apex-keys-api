import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.email_service import send_raffle_canceled_refund_email
from app.live_draw_service import (
    execute_random_draw_for_sold_out_raffle,
    notify_winner_steam_redemption_if_set,
)
from app.models import FeaturedTier, Notification, Raffle, RaffleStatus, Ticket, Transaction, User
from app.pricing import tactical_ticket_price
from app.reservation_service import (
    RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS,
    cancel_hold_reservation,
    expire_stale_pending_reservations,
    finalize_hold_as_paid,
    load_pending_tickets_for_hold,
    purge_stale_raffle_payment_audit_records,
    reservation_expires_at_utc,
)
from app.schemas import (
    AdminDrawRandomOut,
    AdminRaffleCreate,
    AdminRaffleOut,
    AdminReservationRowOut,
    AdminReservationsListOut,
    AdminUserPatch,
    AdminWalletAdjust,
    AdminWalletAdjustResponse,
    AdminWheelSegmentOut,
    AdminWheelSegmentsOut,
    FeaturedTierPatch,
    RaffleCancelResponse,
    RaffleDeleteResponse,
    RaffleDrawRequest,
    RaffleImagePatch,
    RaffleUpdate,
    RaffleVideoPatch,
    UserPublic,
)
from app.security import get_current_admin

router = APIRouter()

_DM_ID_RE = re.compile(
    r"(?:dailymotion\.com/(?:embed/)?video/|dai\.ly/)([a-zA-Z0-9]+)",
    re.I,
)


def _dailymotion_id_from_url(url: str | None) -> str | None:
    """Extrai o ID do vídeo Dailymotion da URL ou devolve o valor se já for um ID (ex.: x8abcd)."""
    if not url or not url.strip():
        return None
    u = url.strip()
    m = _DM_ID_RE.search(u)
    if m:
        return m.group(1)
    if re.fullmatch(r"x[a-zA-Z0-9]{5,32}", u):
        return u
    return None


@router.get("/users", response_model=list[UserPublic])
async def admin_list_users(
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserPublic]:
    """Lista todos os utilizadores ordenados por data de criação (mais recentes primeiro)."""
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    return [UserPublic.model_validate(u) for u in result.scalars().all()]


@router.patch("/users/{user_id}", response_model=UserPublic)
async def admin_patch_user(
    user_id: UUID,
    body: AdminUserPatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    """Atualiza campos de um utilizador (nome, email, whatsapp, pix_key, avatar_url, is_admin)."""
    result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilizador não encontrado")
    data = body.model_dump(exclude_unset=True)
    if "email" in data and data["email"] is not None:
        conflict = await session.execute(
            select(User).where(User.email == str(data["email"]), User.id != user_id)
        )
        if conflict.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já em uso por outro utilizador")
    for field, value in data.items():
        if field == "email" and value is not None:
            setattr(user, field, str(value))
        else:
            setattr(user, field, value)
    await session.flush()
    await session.refresh(user)
    return UserPublic.model_validate(user)


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


@router.post("/raffles", response_model=AdminRaffleOut, status_code=status.HTTP_201_CREATED)
async def create_raffle(
    body: AdminRaffleCreate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    ticket_price = tactical_ticket_price(body.total_price, body.total_tickets)
    ft = body.featured_tier if body.featured_tier in ("featured", "carousel", "none") else FeaturedTier.none.value
    sm = body.summary.strip() if body.summary and body.summary.strip() else None
    igdb_u = body.igdb_url.strip() if body.igdb_url and body.igdb_url.strip() else None
    igdb_gid = body.igdb_game_id.strip() if body.igdb_game_id and body.igdb_game_id.strip() else None
    steam = (
        body.steam_redemption_code.strip()
        if body.steam_redemption_code and body.steam_redemption_code.strip()
        else None
    )
    raffle = Raffle(
        title=body.title,
        image_url=body.image_url,
        video_id=body.video_id,
        total_price=body.total_price,
        total_tickets=body.total_tickets,
        ticket_price=ticket_price,
        status=RaffleStatus.active.value,
        featured_tier=ft,
        summary=sm,
        genres=list(body.genres) if body.genres else None,
        series=list(body.series) if body.series else None,
        game_modes=list(body.game_modes) if body.game_modes else None,
        player_perspectives=list(body.player_perspectives) if body.player_perspectives else None,
        igdb_url=igdb_u,
        igdb_game_id=igdb_gid,
        steam_redemption_code=steam,
    )
    session.add(raffle)
    await session.flush()
    await session.refresh(raffle)
    return AdminRaffleOut.model_validate(raffle)


@router.get("/raffles/{raffle_id}", response_model=AdminRaffleOut)
async def get_raffle_admin(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    result = await session.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    return AdminRaffleOut.model_validate(raffle)


@router.put("/raffles/{raffle_id}", response_model=AdminRaffleOut)
async def update_raffle(
    raffle_id: UUID,
    body: RaffleUpdate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")

    data = body.model_dump(exclude_unset=True)
    if not data:
        return AdminRaffleOut.model_validate(raffle)

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
    if "summary" in data:
        s = data["summary"]
        raffle.summary = s.strip() if isinstance(s, str) and s.strip() else None
    for key in ("genres", "series", "game_modes", "player_perspectives"):
        if key in data and data[key] is not None:
            setattr(raffle, key, list(data[key]))
    if "igdb_url" in data:
        u = data["igdb_url"]
        raffle.igdb_url = u.strip() if isinstance(u, str) and u.strip() else None
    if "igdb_game_id" in data:
        gid = data["igdb_game_id"]
        raffle.igdb_game_id = gid.strip() if isinstance(gid, str) and gid.strip() else None
    if "steam_redemption_code" in data:
        scm = data["steam_redemption_code"]
        if scm is None:
            raffle.steam_redemption_code = None
        elif isinstance(scm, str):
            raffle.steam_redemption_code = scm.strip() if scm.strip() else None
    if "featured_tier" in data:
        val = data["featured_tier"]
        raffle.featured_tier = val if val in ("featured", "carousel", "none") else FeaturedTier.none.value

    await session.flush()
    await session.refresh(raffle)
    return AdminRaffleOut.model_validate(raffle)


@router.patch("/raffles/{raffle_id}/image", response_model=AdminRaffleOut)
async def patch_raffle_image(
    raffle_id: UUID,
    body: RaffleImagePatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
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
    return AdminRaffleOut.model_validate(raffle)


@router.patch("/raffles/{raffle_id}/featured-tier", response_model=AdminRaffleOut)
async def patch_raffle_featured_tier(
    raffle_id: UUID,
    body: FeaturedTierPatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    """
    Atualiza só o featured_tier (estrela).
    Várias rifas podem estar em `featured` (hero com rotação lenta); não há demissão
    automática das outras.
    """
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    raffle.featured_tier = body.featured_tier
    await session.flush()
    await session.refresh(raffle)
    return AdminRaffleOut.model_validate(raffle)


@router.patch("/raffles/{raffle_id}/video", response_model=AdminRaffleOut)
async def patch_raffle_video(
    raffle_id: UUID,
    body: RaffleVideoPatch,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    """
    Atualiza só o campo video_id da rifa (Dailymotion).
    Aceita URL (dailymotion.com/video/…, dai.ly/…) ou só o ID (ex.: x8abcd).
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
        vid = _dailymotion_id_from_url(body.youtube_url)
        if vid is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL ou ID do Dailymotion inválido. Use dailymotion.com/video/…, dai.ly/… ou só o ID (ex.: x8abcd).",
            )
        raffle.video_id = vid
    await session.flush()
    await session.refresh(raffle)
    return AdminRaffleOut.model_validate(raffle)


@router.get("/raffles/{raffle_id}/wheel-segments", response_model=AdminWheelSegmentsOut)
async def admin_wheel_segments(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminWheelSegmentsOut:
    """
    Lista todos os bilhetes pagos da rifa com nome do comprador (para montar a roleta no site).
    Rifas canceladas: 404.
    """
    r_result = await session.execute(select(Raffle).where(Raffle.id == raffle_id))
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status == RaffleStatus.canceled.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rifa cancelada — sem roleta")

    tr = await session.execute(
        select(Ticket, User)
        .join(User, Ticket.user_id == User.id)
        .where(Ticket.raffle_id == raffle_id, Ticket.status == "paid")
        .order_by(Ticket.ticket_number.asc()),
    )
    segments = [
        AdminWheelSegmentOut(
            ticket_number=t.ticket_number,
            user_id=u.id,
            full_name=u.full_name,
            avatar_url=u.avatar_url,
        )
        for t, u in tr.all()
    ]
    return AdminWheelSegmentsOut(
        raffle_id=raffle.id,
        raffle_title=raffle.title,
        raffle_status=raffle.status,
        winning_ticket_number=raffle.winning_ticket_number,
        segments=segments,
    )


@router.post("/raffles/{raffle_id}/draw-random", response_model=AdminDrawRandomOut)
async def admin_draw_random_winner(
    raffle_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminDrawRandomOut:
    """
    Escolhe aleatoriamente (``secrets``) um bilhete pago, regista como vencedor e passa a rifa a ``finished``.
    Mesmas regras que ``POST .../draw`` manual: só ``sold_out`` e sem sorteio prévio.
    """
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status != RaffleStatus.sold_out.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível sortear rifas em estado sold_out",
        )
    if raffle.winning_ticket_number is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta rifa já tem bilhete vencedor registado",
        )

    try:
        n, winner_uid, winner_name = await execute_random_draw_for_sold_out_raffle(session, raffle)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    await session.refresh(raffle)
    return AdminDrawRandomOut(
        raffle=AdminRaffleOut.model_validate(raffle),
        winner_ticket_number=n,
        winner_user_id=winner_uid,
        winner_full_name=winner_name,
    )


@router.post("/raffles/{raffle_id}/draw", response_model=AdminRaffleOut)
async def draw_raffle_winner(
    raffle_id: UUID,
    body: RaffleDrawRequest,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminRaffleOut:
    """
    Regista o bilhete vencedor (tem de existir e estar pago) e passa a rifa a `finished`.
    Só é permitido com rifa em `sold_out` e sem sorteio prévio.
    """
    r_result = await session.execute(
        select(Raffle).where(Raffle.id == raffle_id).with_for_update(),
    )
    raffle = r_result.scalar_one_or_none()
    if raffle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
    if raffle.status != RaffleStatus.sold_out.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Só é possível sortear rifas em estado sold_out",
        )
    if raffle.winning_ticket_number is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta rifa já tem bilhete vencedor registado",
        )
    n = body.winning_ticket_number
    if n < 1 or n > raffle.total_tickets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Número de bilhete inválido para esta rifa (1–{raffle.total_tickets})",
        )
    t_result = await session.execute(
        select(Ticket).where(
            Ticket.raffle_id == raffle_id,
            Ticket.ticket_number == n,
            Ticket.status == "paid",
        ),
    )
    winning_ticket = t_result.scalar_one_or_none()
    if winning_ticket is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não existe bilhete pago com esse número nesta rifa",
        )
    raffle.winning_ticket_number = n
    raffle.drawn_at = datetime.now(timezone.utc)
    raffle.status = RaffleStatus.finished.value
    await session.flush()
    await notify_winner_steam_redemption_if_set(session, raffle, winning_ticket.user_id)
    await session.refresh(raffle)
    return AdminRaffleOut.model_validate(raffle)


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


def _archived_raffle_payment_channel(tx: Transaction) -> str:
    """Canal de auditoria para `raffle_payment` finalizado (QG)."""
    snap = tx.raffle_checkout_snapshot if isinstance(tx.raffle_checkout_snapshot, dict) else {}
    ch = snap.get("payment_channel")
    if ch == "wallet":
        return "wallet"
    if ch == "pix_mp_wallet":
        return "pix_mp_wallet"
    if ch in ("pix_mp", "pix"):
        return "pix_mp"
    if (tx.gateway_reference or "").strip():
        return "pix_mp"
    return "none"


@router.get("/reservations", response_model=AdminReservationsListOut)
async def admin_list_pending_reservations(
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminReservationsListOut:
    """Reservas ativas + histórico de rifa (pago / cancelado / falha) para auditoria."""
    await expire_stale_pending_reservations(session)
    await purge_stale_raffle_payment_audit_records(session)
    tr = await session.execute(
        select(Ticket, User, Raffle)
        .join(User, Ticket.user_id == User.id)
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(
            Ticket.status == "pending_payment",
            Ticket.payment_hold_id.isnot(None),
        )
        .order_by(Ticket.created_at.desc()),
    )
    rows = tr.all()
    by_hold: dict[UUID, list[tuple[Ticket, User, Raffle]]] = defaultdict(list)
    for t, u, r in rows:
        assert t.payment_hold_id is not None
        by_hold[t.payment_hold_id].append((t, u, r))

    active: list[AdminReservationRowOut] = []
    for hold_id, items in by_hold.items():
        t0, user, raffle = items[0]
        nums = sorted(x[0].ticket_number for x in items)
        total = raffle.ticket_price * len(nums)
        tx_r = await session.execute(
            select(Transaction).where(
                Transaction.payment_hold_id == hold_id,
                Transaction.type == "raffle_payment",
            ),
        )
        tx = tx_r.scalar_one_or_none()
        if tx is None:
            channel = "wallet_pending"
        elif tx.status == "pending":
            channel = "pix_mp"
        else:
            channel = "none"
        created_at = min(x[0].created_at for x in items)
        active.append(
            AdminReservationRowOut(
                row_kind="active",
                payment_hold_id=hold_id,
                user_id=user.id,
                user_email=user.email,
                user_name=user.full_name,
                raffle_id=raffle.id,
                raffle_title=raffle.title,
                ticket_numbers=nums,
                total_amount=total,
                created_at=created_at,
                expires_at=reservation_expires_at_utc(created_at),
                payment_channel=channel,
                transaction_id=tx.id if tx else None,
                transaction_status=tx.status if tx else None,
                gateway_reference=tx.gateway_reference if tx else None,
            ),
        )
    active.sort(key=lambda x: x.created_at, reverse=True)

    arch_r = await session.execute(
        select(Transaction, User)
        .join(User, Transaction.user_id == User.id)
        .where(
            Transaction.type == "raffle_payment",
            Transaction.status.in_(("canceled", "failed", "completed")),
        )
        .order_by(Transaction.created_at.desc())
        .limit(250),
    )
    archived: list[AdminReservationRowOut] = []
    for tx, user in arch_r.all():
        snap = tx.raffle_checkout_snapshot if isinstance(tx.raffle_checkout_snapshot, dict) else {}
        nums_raw = snap.get("ticket_numbers")
        nums = [int(n) for n in nums_raw] if isinstance(nums_raw, list) else []
        rid_raw = snap.get("raffle_id")
        raffle_uuid: UUID | None = None
        if isinstance(rid_raw, str):
            try:
                raffle_uuid = UUID(rid_raw)
            except ValueError:
                raffle_uuid = None
        title = snap.get("raffle_title") if isinstance(snap.get("raffle_title"), str) else "—"
        channel_arch = _archived_raffle_payment_channel(tx)
        archived.append(
            AdminReservationRowOut(
                row_kind="archived",
                payment_hold_id=tx.payment_hold_id,
                user_id=user.id,
                user_email=user.email,
                user_name=user.full_name,
                raffle_id=raffle_uuid,
                raffle_title=title,
                ticket_numbers=nums,
                total_amount=tx.amount,
                created_at=tx.created_at,
                expires_at=None,
                payment_channel=channel_arch,
                transaction_id=tx.id,
                transaction_status=tx.status,
                gateway_reference=tx.gateway_reference,
            ),
        )

    return AdminReservationsListOut(active=active, archived=archived)


@router.post("/reservations/{hold_id}/confirm")
async def admin_confirm_reservation(
    hold_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Aprova manualmente: marca bilhetes como pagos (Pix presencial / exceção)."""
    pending = await load_pending_tickets_for_hold(session, hold_id)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma reserva pendente para este hold",
        )
    tx_r = await session.execute(
        select(Transaction).where(
            Transaction.payment_hold_id == hold_id,
            Transaction.type == "raffle_payment",
            Transaction.status == "pending",
        ),
    )
    tx = tx_r.scalar_one_or_none()
    await finalize_hold_as_paid(
        session,
        hold_id,
        mark_raffle_payment_tx_id=tx.id if tx is not None else None,
    )
    await session.commit()
    return {"ok": True, "payment_hold_id": str(hold_id)}


@router.post("/reservations/{hold_id}/cancel")
async def admin_cancel_reservation(
    hold_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Liberta números; mantém registo da transação como cancelada (auditoria)."""
    n = await cancel_hold_reservation(session, hold_id, reason="admin_qg")
    await session.commit()
    return {"released_tickets": n, "payment_hold_id": str(hold_id)}


@router.delete("/transactions/{transaction_id}")
async def admin_delete_raffle_transaction_record(
    transaction_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """
    Apaga permanentemente um registo `raffle_payment` já finalizado, desde que
    tenha mais de RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS na base (retenção legal).
    """
    row = await session.get(Transaction, transaction_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transação não encontrada")
    if row.type != "raffle_payment" or row.status not in ("canceled", "failed", "completed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Só pode apagar registos de rifa finalizados (pago, cancelado ou falha).",
        )
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    threshold = datetime.now(timezone.utc) - timedelta(days=RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS)
    if created > threshold:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Período de retenção: aguarde {RAFFLE_PAYMENT_AUDIT_RETENTION_DAYS} dias após a criação "
                "do registo antes de eliminar."
            ),
        )
    await session.delete(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
