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
from app.mp_logging import webhook_incoming_summary
from app.reservation_service import delete_pending_tickets_for_hold, finalize_hold_as_paid
from app.schemas import MercadoPagoWebhookPayload, WebhookProcessResponse

logger = logging.getLogger("apex_keys")
webhook_log = logging.getLogger("apex_keys.webhook_mp")

router = APIRouter()


async def _credit_pix_deposit(session: AsyncSession, row: Transaction) -> WebhookProcessResponse:
    u_result = await session.execute(select(User).where(User.id == row.user_id).with_for_update())
    user = u_result.scalar_one_or_none()
    if user is None:
        webhook_log.error(
            "[webhook_mp] credit skip user missing transaction_id=%s gateway_ref=%s",
            row.id,
            (row.gateway_reference or "")[:48],
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    user.balance = user.balance + row.amount
    row.status = "completed"
    new_balance = user.balance
    await session.commit()
    webhook_log.info(
        "[webhook_mp] credit ok transaction_id=%s user_id=%s amount=%s new_balance=%s ref=%s",
        row.id,
        row.user_id,
        row.amount,
        new_balance,
        (row.gateway_reference or "")[:48],
    )
    return WebhookProcessResponse(
        transaction_id=row.id,
        user_id=row.user_id,
        amount_credited=row.amount,
        new_balance=new_balance,
    )


async def _finalize_raffle_payment_mp(
    session: AsyncSession,
    row: Transaction,
) -> None:
    """Marca bilhetes do hold como pagos (Pix rifa aprovado). Faz commit."""
    if row.payment_hold_id is None:
        row.status = "failed"
        await session.commit()
        webhook_log.error(
            "[webhook_mp] raffle_payment sem payment_hold_id tx=%s ref=%s",
            row.id,
            (row.gateway_reference or "")[:48],
        )
        return
    tickets, _raffle = await finalize_hold_as_paid(
        session,
        row.payment_hold_id,
        mark_raffle_payment_tx_id=row.id,
    )
    if not tickets:
        row.status = "failed"
        webhook_log.error(
            "[webhook_mp] rifa sem bilhetes pending hold=%s ref=%s",
            row.payment_hold_id,
            (row.gateway_reference or "")[:48],
        )
    await session.commit()
    if tickets:
        webhook_log.info(
            "[webhook_mp] rifa ok tx=%s hold=%s tickets=%s ref=%s",
            row.id,
            row.payment_hold_id,
            len(tickets),
            (row.gateway_reference or "")[:48],
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
        webhook_log.exception(
            "[webhook_mp] get_payment falhou payment_id=%s http=%s detail=%s",
            payment_id,
            e.status_code,
            (e.mp_parsed_detail or str(e))[:400],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao consultar Mercado Pago",
        ) from e

    ext_ref = payment.get("external_reference")
    if not ext_ref:
        webhook_log.warning(
            "[webhook_mp] payment sem external_reference payment_id=%s mp_status=%s",
            payment_id,
            payment.get("status"),
        )
        return

    status_mp = (payment.get("status") or "").lower()
    tr_result = await session.execute(
        select(Transaction)
        .where(Transaction.gateway_reference == str(ext_ref))
        .with_for_update(),
    )
    row = tr_result.scalar_one_or_none()
    if row is None:
        webhook_log.info(
            "[webhook_mp] sem transação local payment_id=%s ext_ref=%s (ignorado)",
            payment_id,
            str(ext_ref)[:48],
        )
        return

    if row.status == "completed":
        webhook_log.info(
            "[webhook_mp] idempotente já completed payment_id=%s ref=%s",
            payment_id,
            str(ext_ref)[:48],
        )
        await session.commit()
        return

    if status_mp in ("rejected", "cancelled", "refunded", "charged_back"):
        webhook_log.info(
            "[webhook_mp] pagamento finalizado negativo payment_id=%s status=%s ref=%s",
            payment_id,
            status_mp,
            str(ext_ref)[:48],
        )
        row.status = "failed"
        if row.type == "raffle_payment" and row.payment_hold_id:
            await delete_pending_tickets_for_hold(session, row.payment_hold_id)
        await session.commit()
        return

    if status_mp != "approved":
        webhook_log.info(
            "[webhook_mp] aguardando outro status payment_id=%s mp_status=%s ref=%s",
            payment_id,
            status_mp,
            str(ext_ref)[:48],
        )
        await session.commit()
        return

    try:
        mp_amount = Decimal(str(payment.get("transaction_amount", "0")))
    except Exception:
        webhook_log.error(
            "[webhook_mp] transaction_amount inválido payment_id=%s raw=%s",
            payment_id,
            payment.get("transaction_amount"),
        )
        await session.commit()
        return

    if mp_amount.quantize(Decimal("0.01")) != row.amount.quantize(Decimal("0.01")):
        webhook_log.error(
            "[webhook_mp] VALOR DIVERGENTE payment_id=%s mp_amount=%s tx_amount=%s ref=%s",
            payment_id,
            mp_amount,
            row.amount,
            str(ext_ref)[:48],
        )
        await session.commit()
        return

    if row.type == "pix_deposit":
        await _credit_pix_deposit(session, row)
    elif row.type == "raffle_payment":
        await _finalize_raffle_payment_mp(session, row)
    else:
        webhook_log.warning(
            "[webhook_mp] tipo de transação não suportado type=%s ref=%s",
            row.type,
            str(ext_ref)[:48],
        )
        await session.commit()


@router.post("/webhook/mp", response_model=WebhookProcessResponse)
async def mercado_pago_webhook(
    body: MercadoPagoWebhookPayload,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResponse:
    """
    Mock / teste manual: com `status=approved`, confirma depósito Pix pendente.
    """
    webhook_log.info(
        "[webhook_mp_mock] gateway_ref=%s status=%s",
        body.gateway_reference[:48],
        body.status,
    )
    tr_result = await session.execute(
        select(Transaction)
        .where(Transaction.gateway_reference == body.gateway_reference)
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
            amount_credited=row.amount if row.type == "pix_deposit" else Decimal("0"),
            new_balance=bal,
        )

    if row.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transação já registrada como falha",
        )

    if body.status != "approved":
        row.status = "failed"
        if row.type == "raffle_payment" and row.payment_hold_id:
            await delete_pending_tickets_for_hold(session, row.payment_hold_id)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pagamento não aprovado (status={body.status})",
        )

    if row.type == "pix_deposit":
        return await _credit_pix_deposit(session, row)

    if row.type == "raffle_payment":
        await _finalize_raffle_payment_mp(session, row)
        u_result = await session.execute(select(User.balance).where(User.id == row.user_id))
        bal = u_result.scalar_one_or_none()
        assert bal is not None
        return WebhookProcessResponse(
            transaction_id=row.id,
            user_id=row.user_id,
            amount_credited=Decimal("0"),
            new_balance=bal,
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Tipo de transação não suportado: {row.type}",
    )


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
        webhook_log.warning("[webhook_mp] POST recebido sem MERCADO_PAGO_ACCESS_TOKEN configurado")
        return {"received": True}

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    payment_id = _payment_id_from_mercadopago_body(body, request.query_params)
    webhook_log.info(
        "[webhook_mp] POST notificação summary=%s payment_id=%s",
        webhook_incoming_summary(body),
        payment_id or "—",
    )
    if not payment_id:
        webhook_log.warning("[webhook_mp] POST sem payment_id ignorado body_keys=%s", list(body.keys())[:12])
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
    webhook_log.info(
        "[webhook_mp] GET notificação topic=%s payment_id=%s",
        request.query_params.get("topic"),
        payment_id or "—",
    )
    if not payment_id:
        return {"received": True}

    await _process_mercadopago_payment_id(session, token, payment_id)
    return {"received": True}
