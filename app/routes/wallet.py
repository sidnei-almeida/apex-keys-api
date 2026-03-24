from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.mercado_pago_service import (
    MercadoPagoApiError,
    create_pix_payment,
    extract_pix_qr_from_payment,
)
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
    """
    Cria depósito Pix pendente. Com `MERCADO_PAGO_ACCESS_TOKEN` (ou `MERCADO_PAGO_ACESS_TOKEN`)
    no ambiente, gera cobrança real via Mercado Pago e devolve QR/ticket; caso contrário, mock.
    """
    dup = await session.execute(
        select(Transaction.id).where(Transaction.gateway_reference == body.gateway_reference),
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="gateway_reference já utilizado",
        )

    settings = get_settings()
    mp_token = (settings.mercado_pago_access_token or "").strip()

    if mp_token:
        u_result = await session.execute(select(User).where(User.id == user_id))
        user = u_result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
        try:
            payment = await create_pix_payment(
                mp_token,
                amount=body.amount,
                payer_email=user.email,
                external_reference=body.gateway_reference,
            )
        except MercadoPagoApiError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=e.args[0],
            ) from e

        tr = Transaction(
            user_id=user_id,
            amount=body.amount,
            type="pix_deposit",
            status="pending",
            gateway_reference=body.gateway_reference,
            description=f"Depósito Pix (Mercado Pago id={payment.get('id')})",
        )
        session.add(tr)
        await session.commit()
        mp_payload = extract_pix_qr_from_payment(payment)
        return {
            "message": "Transação pendente criada (Mercado Pago)",
            "provider": "mercadopago",
            "mercado_pago": mp_payload,
            "mock_pix": None,
        }

    tr = Transaction(
        user_id=user_id,
        amount=body.amount,
        type="pix_deposit",
        status="pending",
        gateway_reference=body.gateway_reference,
        description="Depósito Pix (mock)",
    )
    session.add(tr)
    await session.commit()
    qr = mock_pix_qr_payload(body.gateway_reference, body.amount)
    return {
        "message": "Transação pendente criada (mock)",
        "provider": "mock",
        "mercado_pago": None,
        "mock_pix": qr,
    }
