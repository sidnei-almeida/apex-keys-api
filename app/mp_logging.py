"""Helpers para logs do fluxo Mercado Pago (sem expor tokens)."""

from __future__ import annotations

import json
import logging
from typing import Any

LOG = logging.getLogger("apex_keys.mp")


def mp_body_preview(text: str | None, max_len: int = 800) -> str:
    """Trecho seguro do corpo HTTP (nunca inclui Authorization)."""
    if not text:
        return ""
    t = text.strip().replace("\n", " ")
    if len(t) > max_len:
        return t[:max_len] + "…"
    return t


def mp_parse_error_message(body_text: str | None) -> str:
    """Extrai mensagem legível do JSON de erro do Mercado Pago, se houver."""
    if not body_text:
        return ""
    try:
        data = json.loads(body_text)
    except json.JSONDecodeError:
        return mp_body_preview(body_text, 400)
    if isinstance(data, dict):
        parts: list[str] = []
        if isinstance(data.get("message"), str):
            parts.append(data["message"])
        cause = data.get("cause")
        if isinstance(cause, list):
            for c in cause[:5]:
                if isinstance(c, dict) and isinstance(c.get("description"), str):
                    parts.append(c["description"])
                elif isinstance(c, str):
                    parts.append(c)
        if parts:
            return " | ".join(parts)
    return mp_body_preview(body_text, 400)


def log_mp_create_request(
    *,
    external_reference: str,
    amount: str,
    payer_email_domain: str,
) -> None:
    LOG.info(
        "[mp] create_pix request ext_ref=%s amount=%s payer_domain=%s",
        external_reference[:40] + ("…" if len(external_reference) > 40 else ""),
        amount,
        payer_email_domain or "?",
    )


def log_mp_create_success(
    *,
    payment_id: str | None,
    status: str | None,
    external_reference: str | None,
    has_qr_code: bool,
    has_ticket_url: bool,
) -> None:
    LOG.info(
        "[mp] create_pix ok payment_id=%s status=%s ext_ref=%s qr=%s ticket_url=%s",
        payment_id,
        status,
        (external_reference or "")[:40],
        has_qr_code,
        has_ticket_url,
    )


def log_mp_create_failure(
    *,
    status_code: int,
    body_text: str | None,
    external_reference: str,
) -> None:
    detail = mp_parse_error_message(body_text)
    LOG.error(
        "[mp] create_pix FAIL http=%s ext_ref=%s mp_msg=%s raw=%s",
        status_code,
        external_reference[:40],
        detail,
        mp_body_preview(body_text, 500),
    )


def log_mp_get_payment(
    *,
    payment_id: str,
    ok: bool,
    status_code: int | None = None,
    mp_status: str | None = None,
    external_reference: str | None = None,
    body_text: str | None = None,
) -> None:
    if ok:
        LOG.info(
            "[mp] get_payment ok id=%s mp_status=%s ext_ref=%s",
            payment_id,
            mp_status,
            (external_reference or "")[:40],
        )
    else:
        LOG.error(
            "[mp] get_payment FAIL id=%s http=%s mp_msg=%s",
            payment_id,
            status_code,
            mp_parse_error_message(body_text) or mp_body_preview(body_text, 400),
        )


def payer_email_domain(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" in e:
        return e.split("@", 1)[-1][:80]
    return "?"


def webhook_incoming_summary(body: dict[str, Any]) -> str:
    """Resumo curto do corpo da notificação (para grep)."""
    try:
        return json.dumps(
            {
                "action": body.get("action"),
                "type": body.get("type"),
                "data_id": (body.get("data") or {}).get("id") if isinstance(body.get("data"), dict) else None,
            },
            default=str,
        )
    except Exception:
        return str(body)[:200]
