import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import User
from app.schemas import UserProfileUpdate, UserPublic
from app.security import get_current_user_id

router = APIRouter()

AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2MB
AVATAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _upload_dir() -> Path:
    base = Path(os.getenv("UPLOAD_DIR", "uploads"))
    avatars = base / "avatars"
    avatars.mkdir(parents=True, exist_ok=True)
    return avatars


def _avatar_url(filename: str) -> str:
    """Retorna o path público do avatar (ex.: /uploads/avatars/xxx.webp)."""
    return f"/uploads/avatars/{filename}"


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
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Arquivo muito grande. Máximo: {AVATAR_MAX_BYTES // (1024*1024)}MB",
        )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    upload_dir = _upload_dir()
    filename = f"{user_id}{ext}"
    filepath = upload_dir / filename
    with open(filepath, "wb") as f:
        f.write(content)

    user.avatar_url = _avatar_url(filename)
    await session.flush()
    await session.refresh(user)
    return UserPublic.model_validate(user)
