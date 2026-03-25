import logging
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
from app.schemas import PixDepositAbandon, PixDepositCreate, TransactionOut, WalletBalanceResponse
from app.security import get_current_user_id
from app.utils import mock_pix_qr_payload

router = APIRouter()
logger = logging.getLogger("apex_keys.wallet")


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
        logger.warning(
            "[wallet] mock-pix-intent conflict gateway_ref=%s user_id=%s",
            body.gateway_reference[:48],
            user_id,
        )
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
            logger.error(
                "[wallet] mock-pix-intent MP error user_id=%s amount=%s ref=%s http=%s detail=%s",
                user_id,
                body.amount,
                body.gateway_reference[:48],
                e.status_code,
                (e.mp_parsed_detail or e.args[0])[:300],
            )
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
        logger.info(
            "[wallet] mock-pix-intent ok provider=mercadopago user_id=%s payment_id=%s ref=%s",
            user_id,
            mp_payload.get("payment_id"),
            body.gateway_reference[:48],
        )
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
    logger.info(
        "[wallet] mock-pix-intent ok provider=mock user_id=%s amount=%s ref=%s",
        user_id,
        body.amount,
        body.gateway_reference[:48],
    )
    return {
        "message": "Transação pendente criada (mock)",
        "provider": "mock",
        "mercado_pago": None,
        "mock_pix": qr,
    }


@router.post("/abandon-pix-deposit", status_code=status.HTTP_200_OK)
async def abandon_pix_deposit(
    body: PixDepositAbandon,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """
    Marca um `pix_deposit` pendente como cancelado quando o utilizador para de aguardar no UI.
    O webhook do Mercado Pago ignora crédito se a linha já não estiver `pending`.
    """
    ref = body.gateway_reference.strip()
    tr_result = await session.execute(
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.gateway_reference == ref,
            Transaction.type == "pix_deposit",
        )
        .with_for_update(),
    )
    row = tr_result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transação não encontrada",
        )
    if row.status != "pending":
        return {"message": "Transação já não está pendente", "status": row.status}

    row.status = "canceled"
    extra = " | Desistência (parar de aguardar)"
    row.description = ((row.description or "") + extra)[:500]
    await session.commit()
    logger.info(
        "[wallet] abandon-pix-deposit user_id=%s ref=%s",
        user_id,
        ref[:48],
    )
    return {"message": "Depósito cancelado", "status": "canceled"}
