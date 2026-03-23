from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserSignup(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(..., min_length=1, max_length=255)
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
    full_name: str
    email: EmailStr
    whatsapp: str
    is_admin: bool
    balance: Decimal
    created_at: datetime


class WalletBalanceResponse(BaseModel):
    balance: Decimal


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
    new_balance: Decimal


class RafflePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    image_url: str | None
    video_id: str | None
    total_price: Decimal
    total_tickets: int
    ticket_price: Decimal
    status: Literal["active", "sold_out", "finished", "canceled"]
    created_at: datetime


class RaffleCancelResponse(BaseModel):
    raffle_id: UUID
    status: Literal["canceled"]
    refunds_issued: int


class RaffleDeleteResponse(BaseModel):
    """Resposta após remoção permanente da rifa e dos bilhetes associados."""

    raffle_id: UUID
    tickets_removed: int


class AdminWalletAdjust(BaseModel):
    """Ajuste manual de saldo (admin)."""

    amount: Decimal = Field(..., description="Valor positivo=crédito, negativo=débito")
    description: str | None = Field(None, max_length=500)


class AdminWalletAdjustResponse(BaseModel):
    user_id: UUID
    previous_balance: Decimal
    new_balance: Decimal
    amount_adjusted: Decimal


class AdminRaffleCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., min_length=1, max_length=255)
    image_url: str | None = Field(None, max_length=1024)
    video_id: str | None = Field(None, max_length=64, description="ID do vídeo no YouTube")
    total_price: Decimal = Field(..., gt=0)
    total_tickets: int = Field(..., gt=0)


class RaffleUpdate(BaseModel):
    """Actualização parcial de rifa (admin). `total_price` / `total_tickets` disparam recálculo de `ticket_price`."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=1024)
    video_id: str | None = Field(default=None, max_length=64, description="ID do vídeo no YouTube")
    total_price: Decimal | None = Field(default=None, gt=0)
    total_tickets: int | None = Field(default=None, gt=0)


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
    new_balance: Decimal


class IgdbGameUrlRequest(BaseModel):
    """URL completa da ficha do jogo no IGDB (copiada do browser)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(..., min_length=24, max_length=512, description="https://www.igdb.com/games/...")


class IgdbGameInfoResponse(BaseModel):
    """Dados extraídos por scraping da página pública do jogo no IGDB."""

    slug: str
    name: str | None = None
    title: str | None = None
    summary: str | None = None
    image_url: str | None = None
    youtube_url: str | None = None
    youtube_thumbnail_url: str | None = Field(
        default=None,
        description="Miniatura do trailer (YouTube), derivada do URL quando o HTML público expõe o vídeo",
    )
    igdb_url: str
    igdb_game_id: str | None = None
    genres: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    game_modes: list[str] = Field(default_factory=list)
    player_perspectives: list[str] = Field(default_factory=list)
