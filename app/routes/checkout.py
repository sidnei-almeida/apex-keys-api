from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database import fetch_all, transaction
from app.schemas import (
    RaffleCancelResponse,
    RafflePublic,
    TicketPurchaseRequest,
    TicketPurchaseResponse,
)
from app.security import get_current_admin, get_current_user_id

router = APIRouter()


@router.get("/raffles", response_model=list[RafflePublic])
async def list_raffles(
    status_filter: str | None = Query(None, alias="status", description="open | closed | canceled"),
) -> list[RafflePublic]:
    if status_filter is None:
        rows = await fetch_all(
            """
            SELECT id, title, total_numbers, price_per_number, status, winner_ticket_id, created_at
            FROM raffles
            ORDER BY created_at DESC
            LIMIT 100
            """,
        )
    else:
        s = status_filter.lower().strip()
        if s not in ("open", "closed", "canceled"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status deve ser open, closed ou canceled",
            )
        rows = await fetch_all(
            """
            SELECT id, title, total_numbers, price_per_number, status, winner_ticket_id, created_at
            FROM raffles
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT 100
            """,
            s,
        )
    return [RafflePublic.model_validate(dict(r)) for r in rows]


@router.post("/buy-ticket", response_model=TicketPurchaseResponse)
async def buy_ticket(
    body: TicketPurchaseRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> TicketPurchaseResponse:
    async with transaction() as conn:
        raffle = await conn.fetchrow(
            """
            SELECT id, status, total_numbers, price_per_number, title
            FROM raffles
            WHERE id = $1
            FOR UPDATE
            """,
            body.raffle_id,
        )
        if raffle is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
        if raffle["status"] != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sorteio não está aberto para compras",
            )
        if body.ticket_number > raffle["total_numbers"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Número fora do intervalo do sorteio",
            )

        taken = await conn.fetchrow(
            "SELECT 1 FROM tickets WHERE raffle_id = $1 AND ticket_number = $2",
            body.raffle_id,
            body.ticket_number,
        )
        if taken is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este número já foi vendido",
            )

        user_row = await conn.fetchrow(
            "SELECT id, wallet_balance FROM users WHERE id = $1 FOR UPDATE",
            user_id,
        )
        if user_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

        price = raffle["price_per_number"]
        if user_row["wallet_balance"] < price:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Saldo insuficiente na carteira",
            )

        debit = await conn.execute(
            """
            UPDATE users
            SET wallet_balance = wallet_balance - $1
            WHERE id = $2 AND wallet_balance >= $1
            """,
            price,
            user_id,
        )
        if not debit.startswith("UPDATE 1"):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Saldo insuficiente na carteira",
            )

        try:
            ticket_row = await conn.fetchrow(
                """
                INSERT INTO tickets (raffle_id, user_id, ticket_number, status)
                VALUES ($1, $2, $3, 'paid')
                RETURNING id
                """,
                body.raffle_id,
                user_id,
                body.ticket_number,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Concorrência: número acabou de ser vendido",
            ) from None

        assert ticket_row is not None
        await conn.execute(
            """
            INSERT INTO transactions (user_id, amount, type, status, description)
            VALUES ($1, $2, 'purchase', 'completed', $3)
            """,
            user_id,
            price,
            f"Compra bilhete nº {body.ticket_number} — {raffle['title']}",
        )

        bal = await conn.fetchrow("SELECT wallet_balance FROM users WHERE id = $1", user_id)
        assert bal is not None

    return TicketPurchaseResponse(
        ticket_id=ticket_row["id"],
        raffle_id=body.raffle_id,
        ticket_number=body.ticket_number,
        amount_charged=price,
        new_wallet_balance=bal["wallet_balance"],
    )


@router.post(
    "/admin/raffles/{raffle_id}/cancel",
    response_model=RaffleCancelResponse,
)
async def admin_cancel_raffle(
    raffle_id: UUID,
    _: UUID = Depends(get_current_admin),
) -> RaffleCancelResponse:
    async with transaction() as conn:
        raffle = await conn.fetchrow(
            """
            SELECT id, status, price_per_number, title
            FROM raffles
            WHERE id = $1
            FOR UPDATE
            """,
            raffle_id,
        )
        if raffle is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sorteio não encontrado")
        if raffle["status"] != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Apenas sorteios abertos podem ser cancelados",
            )

        cancel_sql = await conn.execute(
            "UPDATE raffles SET status = 'canceled' WHERE id = $1 AND status = 'open'",
            raffle_id,
        )
        if not cancel_sql.startswith("UPDATE 1"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não foi possível cancelar o sorteio",
            )

        tickets = await conn.fetch(
            """
            SELECT id, user_id, ticket_number
            FROM tickets
            WHERE raffle_id = $1 AND status = 'paid'
            ORDER BY user_id, ticket_number
            FOR UPDATE
            """,
            raffle_id,
        )

        price = raffle["price_per_number"]
        refunds = 0
        for t in tickets:
            await conn.fetchrow("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", t["user_id"])
            await conn.execute(
                """
                UPDATE users
                SET wallet_balance = wallet_balance + $1
                WHERE id = $2
                """,
                price,
                t["user_id"],
            )
            await conn.execute(
                """
                INSERT INTO transactions (user_id, amount, type, status, description)
                VALUES ($1, $2, 'refund', 'completed', $3)
                """,
                t["user_id"],
                price,
                f"Estorno — rifa cancelada ({raffle['title']}) — bilhete nº {t['ticket_number']}",
            )
            refunds += 1

    return RaffleCancelResponse(raffle_id=raffle_id, status="canceled", refunds_issued=refunds)
