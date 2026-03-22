from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Transaction, User
from app.schemas import PixDepositCreate, TransactionOut, WalletBalanceResponse
from app.security import get_current_user_id
from app.utils import mock_pix_qr_payload

router = APIRouter()


@router.get("/balance", response_model=WalletBalanceResponse)
async def get_balance(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> WalletBalanceResponse:
    result = await session.execute(select(User.balance).where(User.id == user_id))
    bal = result.scalar_one_or_none()
    if bal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    return WalletBalanceResponse(balance=bal)


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> list[TransactionOut]:
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .limit(200),
    )
    rows = result.scalars().all()
    return [TransactionOut.model_validate(r) for r in rows]


@router.post("/mock-pix-intent", status_code=status.HTTP_201_CREATED)
async def create_mock_pix_intent(
    body: PixDepositCreate,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    dup = await session.execute(
        select(Transaction.id).where(Transaction.gateway_reference == body.gateway_reference),
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="gateway_reference já utilizado",
        )
    tr = Transaction(
        user_id=user_id,
        amount=body.amount,
        type="pix_deposit",
        status="pending",
        gateway_reference=body.gateway_reference,
        description="Depósito Pix (mock)",
    )
    session.add(tr)
    # Commit explícito: o teardown do Depends corre após a resposta; o webhook no passo
    # seguinte precisa de ver a linha já persistida.
    await session.commit()
    qr = mock_pix_qr_payload(body.gateway_reference, body.amount)
    return {"message": "Transação pendente criada", "mock_pix": qr}
