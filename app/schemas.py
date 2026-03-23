import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _validate_pix_key(v: str | None) -> str | None:
    """Valida chave PIX BR: CPF (11 dígitos), e-mail, telefone E.164 ou UUID (aleatória)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 140:
        raise ValueError("Chave PIX deve ter no máximo 140 caracteres")
    # CPF: 11 dígitos
    if re.fullmatch(r"^[0-9]{11}$", v):
        return v
    # CNPJ: 14 dígitos
    if re.fullmatch(r"^[0-9]{14}$", v):
        return v
    # Telefone E.164: + e 10-15 dígitos
    if re.fullmatch(r"^\+[1-9][0-9]{9,14}$", v):
        return v
    # E-mail (formato básico)
    if re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", v):
        return v
    # Chave aleatória (UUID)
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        v,
    ):
        return v
    raise ValueError(
        "Chave PIX inválida. Use CPF (11 dígitos), e-mail, telefone (+55...) ou chave aleatória (UUID)"
    )


class UserSignup(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    whatsapp: str = Field(..., min_length=10, max_length=20, pattern=r"^\+?[0-9]{10,20}$")
    pix_key: str | None = Field(None, min_length=1, max_length=140)

    @field_validator("pix_key")
    @classmethod
    def validate_pix(cls, v: str | None) -> str | None:
        return _validate_pix_key(v)


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
    pix_key: str | None = None
    avatar_url: str | None = None
    is_admin: bool
    balance: Decimal
    created_at: datetime


class UserProfileUpdate(BaseModel):
    """Atualização parcial do perfil do usuário autenticado."""

    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str | None = Field(None, min_length=1, max_length=255)
    whatsapp: str | None = Field(None, min_length=10, max_length=20, pattern=r"^\+?[0-9]{10,20}$")
    pix_key: str | None = Field(None, max_length=140)

    @field_validator("pix_key")
    @classmethod
    def validate_pix(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            return _validate_pix_key(v)
        return None


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


class RaffleImagePatch(BaseModel):
    """Atualiza só a URL da imagem de capa (ex.: 1080p)."""

    image_url: str | None = Field(default=None, max_length=1024, description="URL da imagem; null para limpar")


class RaffleVideoPatch(BaseModel):
    """Atualiza só o vídeo YouTube. Aceita URL completa ou ID; grava o ID no campo video_id."""

    youtube_url: str | None = Field(
        default=None,
        max_length=512,
        description="URL (watch?v=, youtu.be/, embed/) ou só o video_id; null para limpar",
    )


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
    """Dados extraídos por scraping da página pública do jogo no IGDB (sem imagens nem vídeo — front preenche manualmente)."""

    slug: str
    name: str | None = None
    title: str | None = None
    summary: str | None = None
    igdb_url: str
    igdb_game_id: str | None = None
    genres: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    game_modes: list[str] = Field(default_factory=list)
    player_perspectives: list[str] = Field(default_factory=list)
