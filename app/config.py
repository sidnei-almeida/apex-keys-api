from functools import lru_cache

from pydantic import Field
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

    def cors_origin_list(self) -> list[str]:
        """
        Browsers enviam Origin sem barra final (ex.: https://app.vercel.app).
        Aceita também entradas com / no fim na env para não falhar o preflight OPTIONS.
        """
        if not self.cors_origins.strip():
            return []
        seen: set[str] = set()
        out: list[str] = []
        for raw in self.cors_origins.split(","):
            o = raw.strip().rstrip("/")
            if o and o not in seen:
                seen.add(o)
                out.append(o)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
