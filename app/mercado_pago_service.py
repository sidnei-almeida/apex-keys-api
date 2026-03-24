"""Cliente HTTP para API de pagamentos Mercado Pago (Pix)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.mp_logging import (
    log_mp_create_failure,
    log_mp_create_request,
    log_mp_create_success,
    log_mp_get_payment,
    mp_parse_error_message,
    payer_email_domain,
)

MP_PAYMENTS_URL = "https://api.mercadopago.com/v1/payments"


class MercadoPagoApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
        mp_parsed_detail: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.mp_parsed_detail = mp_parsed_detail


async def create_pix_payment(
    access_token: str,
    *,
    amount: Decimal,
    payer_email: str,
    external_reference: str,
    description: str = "Recarga carteira Apex Keys",
) -> dict[str, Any]:
    """
    Cria pagamento Pix. Retorna o JSON completo da API (inclui id, status, point_of_interaction).
    """
    amount_str = str(amount)
    log_mp_create_request(
        external_reference=external_reference,
        amount=amount_str,
        payer_email_domain=payer_email_domain(payer_email),
    )

    payload = {
        "transaction_amount": float(amount),
        "description": description[:255],
        "payment_method_id": "pix",
        "payer": {"email": payer_email.strip()},
        "external_reference": external_reference[:255],
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4()),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(MP_PAYMENTS_URL, json=payload, headers=headers)
    text = r.text
    if r.status_code >= 400:
        parsed = mp_parse_error_message(text)
        log_mp_create_failure(
            status_code=r.status_code,
            body_text=text,
            external_reference=external_reference,
        )
        raise MercadoPagoApiError(
            f"Mercado Pago rejeitou o pagamento ({r.status_code}): {parsed or 'sem detalhe'}",
            status_code=r.status_code,
            body=text,
            mp_parsed_detail=parsed or None,
        )
    data = r.json()
    ext = data.get("external_reference") or external_reference
    poi = data.get("point_of_interaction") or {}
    td = poi.get("transaction_data") or {}
    log_mp_create_success(
        payment_id=str(data.get("id", "")) or None,
        status=str(data.get("status", "")) or None,
        external_reference=str(ext) if ext else None,
        has_qr_code=bool(td.get("qr_code")),
        has_ticket_url=bool(td.get("ticket_url")),
    )
    return data


async def get_payment(access_token: str, payment_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{MP_PAYMENTS_URL}/{payment_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code >= 400:
        log_mp_get_payment(
            payment_id=payment_id,
            ok=False,
            status_code=r.status_code,
            body_text=r.text,
        )
        raise MercadoPagoApiError(
            f"Não foi possível obter o pagamento ({r.status_code}): {mp_parse_error_message(r.text)}",
            status_code=r.status_code,
            body=r.text,
            mp_parsed_detail=mp_parse_error_message(r.text) or None,
        )
    data = r.json()
    log_mp_get_payment(
        payment_id=payment_id,
        ok=True,
        mp_status=str(data.get("status", "")) or None,
        external_reference=str(data.get("external_reference", "")) or None,
    )
    return data


def extract_pix_qr_from_payment(payment: dict[str, Any]) -> dict[str, Any | None]:
    """Extrai campos de QR/ticket da resposta de criação ou consulta de pagamento."""
    poi = payment.get("point_of_interaction") or {}
    td = poi.get("transaction_data") or {}
    return {
        "payment_id": str(payment.get("id", "")),
        "status": payment.get("status"),
        "qr_code": td.get("qr_code"),
        "qr_code_base64": td.get("qr_code_base64"),
        "ticket_url": td.get("ticket_url"),
    }
