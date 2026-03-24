"""Cliente HTTP para API de pagamentos Mercado Pago (Pix)."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger("apex_keys")

MP_PAYMENTS_URL = "https://api.mercadopago.com/v1/payments"


class MercadoPagoApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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
        logger.warning("Mercado Pago create payment failed: %s %s", r.status_code, text[:500])
        raise MercadoPagoApiError(
            f"Mercado Pago rejeitou o pagamento ({r.status_code})",
            status_code=r.status_code,
            body=text,
        )
    data = r.json()
    return data


async def get_payment(access_token: str, payment_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{MP_PAYMENTS_URL}/{payment_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if r.status_code >= 400:
        raise MercadoPagoApiError(
            f"Não foi possível obter o pagamento ({r.status_code})",
            status_code=r.status_code,
            body=r.text,
        )
    return r.json()


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
