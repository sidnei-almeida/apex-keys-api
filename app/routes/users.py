import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.avatar_image import image_bytes_to_webp_avatar
from app.deps import get_session
from app.models import Notification, Raffle, RaffleStatus, Ticket, User
from app.schemas import MyTicketOut, NotificationOut, RafflePublic, UserProfileUpdate, UserPublic
from app.security import get_current_user_id

router = APIRouter()

# Limite do ficheiro original no upload (pode ser foto gigapixel); depois virá WebP ~384px.
AVATAR_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 20MB
AVATAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _upload_dir() -> Path:
    base = Path(os.getenv("UPLOAD_DIR", "uploads"))
    avatars = base / "avatars"
    avatars.mkdir(parents=True, exist_ok=True)
    return avatars


def _avatar_url(filename: str) -> str:
    """Retorna o path público do avatar (ex.: /uploads/avatars/{uuid}.webp)."""
    return f"/uploads/avatars/{filename}"


def _remove_previous_avatar_files(upload_dir: Path, user_id: UUID) -> None:
    """Remove avatares antigos deste utilizador (outras extensões antes da pipeline WebP)."""
    prefix = str(user_id)
    for p in upload_dir.glob(f"{prefix}.*"):
        try:
            p.unlink()
        except OSError:
            pass


@router.get("/me/tickets", response_model=list[MyTicketOut])
async def list_my_tickets(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filtrar por status da rifa: active, sold_out, finished, canceled",
    ),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> list[MyTicketOut]:
    """Lista bilhetes do usuário com dados da rifa. Use status=active para rifas ativas."""
    query = (
        select(Ticket, Raffle)
        .join(Raffle, Ticket.raffle_id == Raffle.id)
        .where(Ticket.user_id == user_id, Ticket.status.in_(("paid", "pending_payment")))
        .order_by(Ticket.created_at.desc())
    )
    if status_filter:
        s = status_filter.lower().strip()
        if s in {st.value for st in RaffleStatus}:
            query = query.where(Raffle.status == s)
    result = await session.execute(query)
    rows = result.all()
    return [
        MyTicketOut(
            ticket_id=t.id,
            raffle_id=t.raffle_id,
            ticket_number=t.ticket_number,
            status=t.status if t.status in ("paid", "pending_payment") else "paid",
            raffle=RafflePublic.model_validate(r),
            created_at=t.created_at,
        )
        for t, r in rows
    ]


@router.get("/me/notifications", response_model=list[NotificationOut])
async def list_my_notifications(
    unread_only: bool = Query(False, alias="unread_only"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> list[NotificationOut]:
    """Lista notificações do usuário, ordenadas por mais recente."""
    query = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    result = await session.execute(query)
    notifications = result.scalars().all()
    return [NotificationOut.model_validate(n) for n in notifications]


@router.get("/me/notifications/unread-count")
async def get_unread_notifications_count(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Retorna a quantidade de notificações não lidas."""
    result = await session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    count = int(result.scalar() or 0)
    return {"unread_count": count}


@router.patch("/me/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> NotificationOut:
    """Marca uma notificação como lida."""
    result = await session.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notificação não encontrada")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await session.flush()
        await session.refresh(notification)
    return NotificationOut.model_validate(notification)


@router.post("/me/notifications/read-all")
async def mark_all_notifications_read(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Marca todas as notificações do usuário como lidas."""
    result = await session.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    notifications = result.scalars().all()
    now = datetime.now(timezone.utc)
    for n in notifications:
        n.read_at = now
    await session.flush()
    return {"message": "Todas as notificações foram marcadas como lidas"}


@router.patch("/me", response_model=UserPublic)
async def update_me(
    body: UserProfileUpdate,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    data = body.model_dump(exclude_unset=True)
    if "pix_key" in data and data["pix_key"] == "":
        data["pix_key"] = None

    # Se whatsapp for atualizado, verificar unicidade
    if "whatsapp" in data and data["whatsapp"] != user.whatsapp:
        dup = await session.execute(select(User.id).where(User.whatsapp == data["whatsapp"]).limit(1))
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="WhatsApp já cadastrado",
            )

    for key, value in data.items():
        setattr(user, key, value)
    await session.flush()
    await session.refresh(user)
    return UserPublic.model_validate(user)


@router.post("/me/avatar", response_model=UserPublic)
async def upload_avatar(
    file: UploadFile = File(...),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato inválido. Use: {', '.join(AVATAR_ALLOWED_EXTENSIONS)}",
        )

    content = await file.read()
    if len(content) > AVATAR_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Arquivo muito grande. Máximo: {AVATAR_UPLOAD_MAX_BYTES // (1024 * 1024)}MB antes da otimização.",
        )

    try:
        webp_bytes = image_bytes_to_webp_avatar(content)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    upload_dir = _upload_dir()
    _remove_previous_avatar_files(upload_dir, user_id)
    filename = f"{user_id}.webp"
    filepath = upload_dir / filename
    with open(filepath, "wb") as f:
        f.write(webp_bytes)

    user.avatar_url = _avatar_url(filename)
    await session.flush()
    await session.refresh(user)
    return UserPublic.model_validate(user)
