from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Ticket, Transaction, User


async def purge_due_deletions(session: AsyncSession) -> int:
    """
    Apaga definitivamente contas cujo delete_after já expirou.
    Retorna a quantidade de utilizadores apagados.
    """
    now = datetime.now(UTC)
    r = await session.execute(select(User.id).where(User.delete_after.isnot(None), User.delete_after <= now))
    ids = [row[0] for row in r.fetchall()]
    if not ids:
        return 0
    for uid in ids:
        await purge_user_account(session, uid)
    return len(ids)


async def purge_user_account(session: AsyncSession, user_id: UUID) -> None:
    """
    Remove dados associados e a linha do utilizador.
    Ordem importa por FKs.
    """
    await session.execute(delete(Notification).where(Notification.user_id == user_id))
    await session.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await session.execute(delete(Ticket).where(Ticket.user_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))

