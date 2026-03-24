import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_session
from app.mercado_pago_service import MercadoPagoApiError, get_payment
from app.models import Transaction, User
from app.schemas import MercadoPagoWebhookPayload, WebhookProcessResponse

logger = logging.getLogger("apex_keys")

router = APIRouter()


async def _credit_pix_deposit(session: AsyncSession, row: Transaction) -> WebhookProcessResponse:
    u_result = await session.execute(select(User).where(User.id == row.user_id).with_for_update())
    user = u_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    user.balance = user.balance + row.amount
    row.status = "completed"
    new_balance = user.balance
    await session.commit()
    return WebhookProcessResponse(
        transaction_id=row.id,
        user_id=row.user_id,
        amount_credited=row.amount,
        new_balance=new_balance,
    )


def _payment_id_from_mercadopago_body(body: dict[str, Any], query_params) -> str | None:
    data = body.get("data")
    if isinstance(data, dict) and data.get("id") is not None:
        return str(data["id"])
    if isinstance(data, str) and data.isdigit():
        return data
    topic = (query_params.get("topic") or "").lower()
    qid = query_params.get("id") or query_params.get("data.id")
    if topic == "payment" and qid:
        return str(qid)
    return None


async def _process_mercadopago_payment_id(
    session: AsyncSession,
    access_token: str,
    payment_id: str,
) -> None:
    try:
        payment = await get_payment(access_token, payment_id)
    except MercadoPagoApiError as e:
        logger.exception("Falha ao consultar pagamento MP %s: %s", payment_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao consultar Mercado Pago",
        ) from e

    ext_ref = payment.get("external_reference")
    if not ext_ref:
        return

    status_mp = (payment.get("status") or "").lower()
    tr_result = await session.execute(
        select(Transaction)
        .where(
            Transaction.gateway_reference == str(ext_ref),
            Transaction.type == "pix_deposit",
        )
        .with_for_update(),
    )
    row = tr_result.scalar_one_or_none()
    if row is None:
        return

    if row.status == "completed":
        await session.commit()
        return

    if status_mp in ("rejected", "cancelled", "refunded", "charged_back"):
        row.status = "failed"
        await session.commit()
        return

    if status_mp != "approved":
        await session.commit()
        return

    try:
        mp_amount = Decimal(str(payment.get("transaction_amount", "0")))
    except Exception:
        logger.error("transaction_amount inválido no pagamento MP %s", payment_id)
        await session.commit()
        return

    if mp_amount != row.amount:
        logger.error(
            "Valor MP (%s) ≠ transação (%s) ref=%s",
            mp_amount,
            row.amount,
            ext_ref,
        )
        await session.commit()
        return

    await _credit_pix_deposit(session, row)


@router.post("/webhook/mp", response_model=WebhookProcessResponse)
async def mercado_pago_webhook(
    body: MercadoPagoWebhookPayload,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResponse:
    """
    Mock / teste manual: com `status=approved`, confirma depósito Pix pendente.
    """
    tr_result = await session.execute(
        select(Transaction)
        .where(
            Transaction.gateway_reference == body.gateway_reference,
            Transaction.type == "pix_deposit",
        )
        .with_for_update(),
    )
    row = tr_result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transação não encontrada para este gateway_reference",
        )

    if row.status == "completed":
        u_result = await session.execute(select(User.balance).where(User.id == row.user_id))
        bal = u_result.scalar_one_or_none()
        assert bal is not None
        await session.commit()
        return WebhookProcessResponse(
            transaction_id=row.id,
            user_id=row.user_id,
            amount_credited=row.amount,
            new_balance=bal,
        )

    if row.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transação já registrada como falha",
        )

    if body.status != "approved":
        row.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pagamento não aprovado (status={body.status})",
        )

    return await _credit_pix_deposit(session, row)


@router.post("/webhook/mercadopago")
async def mercadopago_ipn_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """
    Notificação enviada pelo Mercado Pago (pagamento criado/atualizado).
    """
    settings = get_settings()
    token = (settings.mercado_pago_access_token or "").strip()
    if not token:
        logger.warning("Webhook Mercado Pago recebido mas token não configurado")
        return {"received": True}

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    payment_id = _payment_id_from_mercadopago_body(body, request.query_params)
    if not payment_id:
        return {"received": True}

    await _process_mercadopago_payment_id(session, token, payment_id)
    return {"received": True}


@router.get("/webhook/mercadopago")
async def mercadopago_ipn_get(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """IPN legado (?topic=payment&id=...)."""
    settings = get_settings()
    token = (settings.mercado_pago_access_token or "").strip()
    if not token:
        return {"received": True}

    payment_id = _payment_id_from_mercadopago_body({}, request.query_params)
    if not payment_id:
        return {"received": True}

    await _process_mercadopago_payment_id(session, token, payment_id)
    return {"received": True}
