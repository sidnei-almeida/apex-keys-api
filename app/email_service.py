"""Serviço de envio de emails via Resend API."""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger("apex_keys")

RESEND_API_URL = "https://api.resend.com/emails"


async def send_raffle_canceled_refund_email(
    to_email: str,
    full_name: str,
    raffle_title: str,
    amount_refunded: str,
) -> bool:
    """
    Envia email informando que a rifa foi cancelada e o valor creditado na carteira.
    Retorna True se enviado com sucesso, False caso contrário.
    """
    settings = get_settings()
    if not settings.resend_api_key or not settings.resend_api_key.strip():
        logger.warning("RESEND_API_KEY não configurada — email de estorno não enviado")
        return False

    transactions_url = ""
    if settings.frontend_url:
        base = settings.frontend_url.rstrip("/")
        transactions_url = f"{base}/minhas-transacoes"

    subject = f"Rifa cancelada — {raffle_title}"
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: system-ui, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
  <h2 style="color: #0A111F;">Rifa cancelada</h2>
  <p>Olá, {full_name}!</p>
  <p>A rifa <strong>{raffle_title}</strong> foi cancelada.</p>
  <p>O valor de <strong>{amount_refunded}</strong> foi creditado na sua carteira.</p>
  <p>Você pode utilizar esse saldo em outras rifas ou solicitar o saque.</p>
  {"<p><a href=\"" + transactions_url + "\" style=\"color: #00E5FF;\">Ver minhas transações</a></p>" if transactions_url else ""}
  <p style="margin-top: 30px; color: #666; font-size: 14px;">— Apex Keys</p>
</body>
</html>
""".strip()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.email_from,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                },
            )
            if resp.status_code in (200, 201, 202):
                return True
            logger.error(
                "Resend API falhou: status=%s body=%s",
                resp.status_code,
                resp.text[:500],
            )
            return False
    except Exception as e:
        logger.exception("Erro ao enviar email via Resend: %s", e)
        return False
