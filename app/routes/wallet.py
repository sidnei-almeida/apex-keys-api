from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import execute, fetch_all, fetch_one
from app.schemas import PixDepositCreate, TransactionOut, WalletBalanceResponse
from app.security import get_current_user_id
from app.utils import mock_pix_qr_payload

router = APIRouter()


@router.get("/balance", response_model=WalletBalanceResponse)
async def get_balance(user_id: UUID = Depends(get_current_user_id)) -> WalletBalanceResponse:
    row = await fetch_one("SELECT wallet_balance FROM users WHERE id = $1", user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    return WalletBalanceResponse(wallet_balance=row["wallet_balance"])


@router.get("/transactions", response_model=list[TransactionOut])
async def list_transactions(user_id: UUID = Depends(get_current_user_id)) -> list[TransactionOut]:
    rows = await fetch_all(
        """
        SELECT id, amount, type, status, gateway_reference, description, created_at
        FROM transactions
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT 200
        """,
        user_id,
    )
    return [TransactionOut.model_validate(dict(r)) for r in rows]


@router.post("/mock-pix-intent", status_code=status.HTTP_201_CREATED)
async def create_mock_pix_intent(
    body: PixDepositCreate,
    user_id: UUID = Depends(get_current_user_id),
) -> dict:
    """
    Cria uma transação `pix_deposit` pendente para testar o webhook.
    Em produção, este fluxo viria do gateway real.
    """
    dup = await fetch_one(
        "SELECT id FROM transactions WHERE gateway_reference = $1",
        body.gateway_reference,
    )
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="gateway_reference já utilizado",
        )
    await execute(
        """
        INSERT INTO transactions (user_id, amount, type, status, gateway_reference, description)
        VALUES ($1, $2, 'pix_deposit', 'pending', $3, 'Depósito Pix (mock)')
        """,
        user_id,
        body.amount,
        body.gateway_reference,
    )
    qr = mock_pix_qr_payload(body.gateway_reference, body.amount)
    return {"message": "Transação pendente criada", "mock_pix": qr}
