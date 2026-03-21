from fastapi import APIRouter, HTTPException, status

from app.database import transaction
from app.schemas import MercadoPagoWebhookPayload, WebhookProcessResponse

router = APIRouter()


@router.post("/webhook/mp", response_model=WebhookProcessResponse)
async def mercado_pago_webhook(body: MercadoPagoWebhookPayload) -> WebhookProcessResponse:
    """
    Mock do webhook do Mercado Pago: com `status=approved`, confirma depósito Pix pendente.
    Em produção, valide assinatura do provedor e idempotência por `gateway_reference`.
    """
    rejected: bool = False

    async with transaction() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, amount, status
            FROM transactions
            WHERE gateway_reference = $1 AND type = 'pix_deposit'
            FOR UPDATE
            """,
            body.gateway_reference,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transação não encontrada para este gateway_reference",
            )

        if row["status"] == "completed":
            bal = await conn.fetchrow("SELECT wallet_balance FROM users WHERE id = $1", row["user_id"])
            assert bal is not None
            return WebhookProcessResponse(
                transaction_id=row["id"],
                user_id=row["user_id"],
                amount_credited=row["amount"],
                new_wallet_balance=bal["wallet_balance"],
            )

        if row["status"] == "failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transação já registrada como falha",
            )

        if body.status != "approved":
            await conn.execute(
                "UPDATE transactions SET status = 'failed' WHERE id = $1 AND status = 'pending'",
                row["id"],
            )
            rejected = True
        else:
            await conn.fetchrow("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", row["user_id"])
            await conn.execute(
                """
                UPDATE users
                SET wallet_balance = wallet_balance + $1
                WHERE id = $2
                """,
                row["amount"],
                row["user_id"],
            )
            await conn.execute(
                "UPDATE transactions SET status = 'completed' WHERE id = $1",
                row["id"],
            )
            bal = await conn.fetchrow("SELECT wallet_balance FROM users WHERE id = $1", row["user_id"])
            assert bal is not None
            return WebhookProcessResponse(
                transaction_id=row["id"],
                user_id=row["user_id"],
                amount_credited=row["amount"],
                new_wallet_balance=bal["wallet_balance"],
            )

    if rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pagamento não aprovado (status={body.status})",
        )
