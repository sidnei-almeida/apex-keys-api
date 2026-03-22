from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import User
from app.schemas import TokenResponse, UserLogin, UserPublic, UserSignup
from app.security import create_access_token, get_current_user_id, hash_password, verify_password

router = APIRouter()


@router.post("/signup", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def signup(body: UserSignup, session: AsyncSession = Depends(get_session)) -> UserPublic:
    dup = await session.execute(
        select(User.id)
        .where(or_(User.email == body.email, User.whatsapp == body.whatsapp))
        .limit(1),
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="E-mail ou WhatsApp já cadastrado",
        )
    user = User(
        full_name=body.full_name,
        email=body.email,
        password_hash=hash_password(body.password),
        whatsapp=body.whatsapp,
        balance=Decimal("0.00"),
        is_admin=False,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return UserPublic.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos",
        )
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserPublic)
async def me(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> UserPublic:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    return UserPublic.model_validate(user)
