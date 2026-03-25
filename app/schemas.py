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
    deactivated_at: datetime | None = None
    delete_after: datetime | None = None


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
    type: Literal[
        "pix_deposit",
        "purchase",
        "refund",
        "admin_adjustment",
        "raffle_payment",
    ]
    status: Literal["pending", "completed", "failed", "canceled"]
    gateway_reference: str | None
    description: str | None
    payment_hold_id: UUID | None = None
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    body: str
    read_at: datetime | None
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


class ReserveRaffleTicketsBody(BaseModel):
    raffle_id: UUID
    ticket_numbers: list[int] = Field(..., min_length=1, max_length=50)


class ReserveRaffleTicketsResponse(BaseModel):
    payment_hold_id: UUID
    raffle_id: UUID
    ticket_numbers: list[int]
    total_amount: Decimal


class CompleteReservationWalletBody(BaseModel):
    payment_hold_id: UUID


class ReservationPixIntentBody(BaseModel):
    payment_hold_id: UUID
    gateway_reference: str = Field(..., min_length=8, max_length=255)


class ReservationStatusOut(BaseModel):
    payment_hold_id: UUID
    state: Literal["pending_payment", "paid", "released", "unknown"]
    raffle_id: UUID | None = None
    ticket_numbers: list[int] = Field(default_factory=list)
    transaction_status: Literal["pending", "completed", "failed", "canceled"] | None = None
    gateway_reference: str | None = None


class AdminReservationRowOut(BaseModel):
    """active = reserva com bilhetes pending; archived = registo raffle_payment finalizado (auditoria)."""

    row_kind: Literal["active", "archived"] = "active"
    payment_hold_id: UUID | None = None
    user_id: UUID
    user_email: str
    user_name: str
    raffle_id: UUID | None = None
    raffle_title: str
    ticket_numbers: list[int] = Field(default_factory=list)
    total_amount: Decimal
    created_at: datetime
    expires_at: datetime | None = Field(
        None,
        description="Só active: fim da janela de 15 min antes da expiração automática",
    )
    payment_channel: Literal[
        "pix",
        "pix_mp",
        "wallet",
        "wallet_pending",
        "none",
        "pix_mp_wallet",
    ]
    transaction_id: UUID | None = None
    transaction_status: Literal["pending", "completed", "failed", "canceled"] | None = None
    gateway_reference: str | None = None


class AdminReservationsListOut(BaseModel):
    active: list[AdminReservationRowOut]
    archived: list[AdminReservationRowOut]


FeaturedTierType = Literal["featured", "carousel", "none"]


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
    featured_tier: FeaturedTierType | None = None
    winning_ticket_number: int | None = None
    drawn_at: datetime | None = None
    created_at: datetime
    summary: str | None = None
    genres: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    game_modes: list[str] = Field(default_factory=list)
    player_perspectives: list[str] = Field(default_factory=list)
    igdb_url: str | None = None
    igdb_game_id: str | None = None

    @field_validator("genres", "series", "game_modes", "player_perspectives", mode="before")
    @classmethod
    def _coerce_str_lists(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out


class RaffleListOut(RafflePublic):
    """RafflePublic + vendidos e reservas ativas (pending_payment)."""

    sold: int = 0
    held: int = Field(
        0,
        description="Bilhetes reservados aguardando pagamento (números bloqueados na grade)",
    )


class RaffleDetailOut(RaffleListOut):
    """RaffleListOut + números vendidos e reservados (aguardando pagamento)."""

    sold_numbers: list[int] = Field(default_factory=list)
    held_numbers: list[int] = Field(
        default_factory=list,
        description="Números com reserva ativa (pending_payment), indisponíveis na grade",
    )


class MyTicketOut(BaseModel):
    """Bilhete do usuário com dados da rifa."""

    model_config = ConfigDict(from_attributes=True)

    ticket_id: UUID
    raffle_id: UUID
    ticket_number: int
    status: Literal["paid", "pending_payment"] = "paid"
    raffle: RafflePublic
    created_at: datetime


class RaffleCancelResponse(BaseModel):
    raffle_id: UUID
    status: Literal["canceled"]
    refunds_issued: int


class RaffleDeleteResponse(BaseModel):
    """Resposta após remoção permanente da rifa e dos bilhetes associados."""

    raffle_id: UUID
    tickets_removed: int


class RaffleDrawRequest(BaseModel):
    """Regista o bilhete vencedor e encerra a rifa (`finished`)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    winning_ticket_number: int = Field(..., ge=1, description="Número do bilhete pago que ganhou o sorteio")


class HallOfFameSpotlightRaffle(BaseModel):
    """Rifa de destaque no cartão (ex.: última vitória do utilizador)."""

    raffle_id: UUID
    title: str
    image_url: str | None = None
    winning_ticket_number: int


class HallOfFameEntryOut(BaseModel):
    """Uma posição no ranking (1 = campeão)."""

    rank: int = Field(..., ge=1, le=5)
    user_id: UUID
    full_name: str
    avatar_url: str | None = None
    wins: int = Field(..., ge=1)
    spotlight: HallOfFameSpotlightRaffle


class RecentPurchasePulseOut(BaseModel):
    """Compra agregada para prova social (nome parcial, sem e-mail)."""

    id: str
    display_name: str
    quantity: int = Field(..., ge=1)
    raffle_title: str
    purchased_at: datetime


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
    video_id: str | None = Field(None, max_length=64, description="ID do vídeo no Dailymotion (ex.: x8abcd)")
    total_price: Decimal = Field(..., gt=0)
    total_tickets: int = Field(..., gt=0)
    featured_tier: FeaturedTierType | None = Field(
        None,
        description=(
            "featured=hero home (várias rifas permitidas; ordem pública = created_at asc), "
            "carousel=carrossel, none=só em /rifas"
        ),
    )
    summary: str | None = Field(None, max_length=16000)
    genres: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    game_modes: list[str] = Field(default_factory=list)
    player_perspectives: list[str] = Field(default_factory=list)
    igdb_url: str | None = Field(None, max_length=1024)
    igdb_game_id: str | None = Field(None, max_length=64)


class RaffleUpdate(BaseModel):
    """Actualização parcial de rifa (admin). `total_price` / `total_tickets` disparam recálculo de `ticket_price`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    image_url: str | None = Field(default=None, max_length=1024)
    video_id: str | None = Field(default=None, max_length=64, description="ID do vídeo no Dailymotion (ex.: x8abcd)")
    total_price: Decimal | None = Field(default=None, gt=0)
    total_tickets: int | None = Field(default=None, gt=0)
    featured_tier: FeaturedTierType | None = Field(
        default=None,
        description=(
            "featured=hero home (várias rifas permitidas), carousel=carrossel, none=só em /rifas"
        ),
    )
    summary: str | None = Field(default=None, max_length=16000)
    genres: list[str] | None = None
    series: list[str] | None = None
    game_modes: list[str] | None = None
    player_perspectives: list[str] | None = None
    igdb_url: str | None = Field(default=None, max_length=1024)
    igdb_game_id: str | None = Field(default=None, max_length=64)


class RaffleImagePatch(BaseModel):
    """Atualiza só a URL da imagem de capa (ex.: 1080p)."""

    image_url: str | None = Field(default=None, max_length=1024, description="URL da imagem; null para limpar")


class FeaturedTierPatch(BaseModel):
    """Atualiza só o featured_tier (posição na home). Várias rifas podem ser `featured`."""

    featured_tier: Literal["featured", "carousel", "none"] = Field(
        ...,
        description=(
            "featured=hero home (múltiplas permitidas), carousel=carrossel, none=só em /rifas"
        ),
    )


class RaffleVideoPatch(BaseModel):
    """Atualiza só o trailer Dailymotion. Aceita URL completa ou ID; grava o video_id."""

    youtube_url: str | None = Field(
        default=None,
        max_length=512,
        description="URL Dailymotion (dailymotion.com/video/…, dai.ly/…) ou só o ID; null para limpar",
    )


class PixDepositCreate(BaseModel):
    """Cria transação pendente para testes do webhook (valor em créditos)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    amount: Decimal = Field(..., gt=0)
    gateway_reference: str = Field(..., min_length=1, max_length=255)


class PixDepositAbandon(BaseModel):
    """Utilizador fechou o modal de depósito (parar de aguardar)."""

    model_config = ConfigDict(str_strip_whitespace=True)

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
