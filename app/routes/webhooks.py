from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_session
from app.models import Transaction, User
from app.schemas import MercadoPagoWebhookPayload, WebhookProcessResponse

router = APIRouter()


@router.post("/webhook/mp", response_model=WebhookProcessResponse)
async def mercado_pago_webhook(
    body: MercadoPagoWebhookPayload,
    session: AsyncSession = Depends(get_session),
) -> WebhookProcessResponse:
    """
    Mock do webhook do Mercado Pago: com `status=approved`, confirma depósito Pix pendente.
    Em produção, valide assinatura do provedor e idempotência por `gateway_reference`.
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
