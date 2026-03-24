from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:5432/apex_keys",
        description="DSN PostgreSQL (obrigatório em produção)",
    )
    database_ssl_no_verify: bool = Field(
        default=False,
        description="Desactiva verificação SSL do Postgres (ex.: Railway local; não usar em produção pública sem avaliar risco)",
    )
    jwt_secret: str = Field(
        default="altere-em-producao-use-um-segredo-longo",
        description="Segredo JWT (obrigatório em produção)",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    cors_origins: str = ""
    igdb_initial_delay_sec: float = Field(
        default=5.0,
        description="Atraso em segundos antes do primeiro pedido ao IGDB (evitar sobrecarga Cloudflare)",
    )
    igdb_max_retries: int = Field(
        default=2,
        description="Tentativas extras se pedido falhar ou HTML vier vazio/desafio (total = 1 + este valor)",
    )
    resend_api_key: str | None = Field(
        default=None,
        description="API key do Resend para envio de emails. Se vazio, emails não são enviados.",
    )
    email_from: str = Field(
        default="noreply@apexkeys.com",
        description="Email remetente (domínio verificado no Resend)",
    )
    frontend_url: str = Field(
        default="",
        description="URL base do frontend para links em emails (ex: https://apexkeys.com)",
    )
    mercado_pago_access_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MERCADO_PAGO_ACCESS_TOKEN",
            "MERCADO_PAGO_ACESS_TOKEN",
        ),
        description="Access token da API Mercado Pago (teste ou produção). Opcional: sem token usa mock Pix.",
    )

    def cors_origin_list(self) -> list[str]:
        """
        Browsers enviam Origin sem barra final (ex.: https://app.vercel.app).
        Aceita também entradas com / no fim na env para não falhar o preflight OPTIONS.
        CORS_ORIGINS=* permite qualquer origem (útil em dev; em produção prefira lista explícita).
        """
        raw = self.cors_origins.strip()
        if not raw:
            return []
        if raw == "*":
            return ["*"]
        seen: set[str] = set()
        out: list[str] = []
        for part in raw.split(","):
            o = part.strip().rstrip("/")
            if o and o not in seen:
                seen.add(o)
                out.append(o)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
