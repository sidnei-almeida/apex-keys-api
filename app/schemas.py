from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    whatsapp: str = Field(..., min_length=10, max_length=20, pattern=r"^\+?[0-9]{10,20}$")


class UserLogin(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: EmailStr
    whatsapp: str
    role: Literal["user", "admin"]
    wallet_balance: Decimal
    created_at: datetime


class WalletBalanceResponse(BaseModel):
    wallet_balance: Decimal


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount: Decimal
    type: Literal["pix_deposit", "purchase", "refund", "admin_adjustment"]
    status: Literal["pending", "completed", "failed"]
    gateway_reference: str | None
    description: str | None
    created_at: datetime


class TicketPurchaseRequest(BaseModel):
    raffle_id: UUID
    ticket_number: int = Field(..., ge=1)


class TicketPurchaseResponse(BaseModel):
    ticket_id: UUID
    raffle_id: UUID
    ticket_number: int
    amount_charged: Decimal
    new_wallet_balance: Decimal


class RafflePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    total_numbers: int
    price_per_number: Decimal
    status: Literal["open", "closed", "canceled"]
    winner_ticket_id: UUID | None
    created_at: datetime


class RaffleCancelResponse(BaseModel):
    raffle_id: UUID
    status: Literal["canceled"]
    refunds_issued: int


class PixDepositCreate(BaseModel):
    """Cria transação pendente para testes do webhook (valor em créditos)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    amount: Decimal = Field(..., gt=0)
    gateway_reference: str = Field(..., min_length=1, max_length=255)


class MercadoPagoWebhookPayload(BaseModel):
    """Mock do payload essencial do Mercado Pago."""

    model_config = ConfigDict(str_strip_whitespace=True)

    gateway_reference: str = Field(..., min_length=1, max_length=255)
    status: Literal["approved", "pending", "rejected", "cancelled"] = "approved"

    @field_validator("status")
    @classmethod
    def normalize_status(cls, v: str) -> str:
        return v.lower()


class WebhookProcessResponse(BaseModel):
    transaction_id: UUID
    user_id: UUID
    amount_credited: Decimal
    new_wallet_balance: Decimal
