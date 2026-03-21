from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import execute, fetch_one
from app.schemas import TokenResponse, UserCreate, UserLogin, UserPublic
from app.security import create_access_token, get_current_user_id, hash_password, verify_password

router = APIRouter()


@router.post("/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate) -> UserPublic:
    existing = await fetch_one(
        "SELECT id FROM users WHERE email = $1 OR whatsapp = $2",
        body.email,
        body.whatsapp,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="E-mail ou WhatsApp já cadastrado",
        )
    hashed = hash_password(body.password)
    row = await fetch_one(
        """
        INSERT INTO users (name, email, hashed_password, whatsapp, role, wallet_balance)
        VALUES ($1, $2, $3, $4, 'user', 0.00)
        RETURNING id, name, email, whatsapp, role, wallet_balance, created_at
        """,
        body.name,
        body.email,
        hashed,
        body.whatsapp,
    )
    assert row is not None
    return UserPublic.model_validate(dict(row))


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin) -> TokenResponse:
    row = await fetch_one(
        "SELECT id, hashed_password, role FROM users WHERE email = $1",
        body.email,
    )
    if row is None or not verify_password(body.password, row["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha incorretos",
        )
    token = create_access_token(
        str(row["id"]),
        extra_claims={"role": row["role"]},
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserPublic)
async def me(user_id: UUID = Depends(get_current_user_id)) -> UserPublic:
    row = await fetch_one(
        """
        SELECT id, name, email, whatsapp, role, wallet_balance, created_at
        FROM users WHERE id = $1
        """,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    return UserPublic.model_validate(dict(row))
