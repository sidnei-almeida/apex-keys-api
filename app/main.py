import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import close_db, init_db
from app.routes import admin, auth, checkout, igdb, rankings, raffle_reservations, users, wallet, webhooks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("apex_keys")


def _json_safe_validation_errors(errors: list) -> list:
    """Pydantic v2 pode colocar exceções em `ctx`; json.dumps não as serializa."""
    out: list[dict] = []
    for item in errors:
        if not isinstance(item, dict):
            out.append({"msg": str(item)})
            continue
        d = dict(item)
        ctx = d.get("ctx")
        if isinstance(ctx, dict):
            safe_ctx: dict = {}
            for k, val in ctx.items():
                if isinstance(val, BaseException):
                    safe_ctx[k] = str(val)
                elif isinstance(val, (str, int, float, bool, type(None))):
                    safe_ctx[k] = val
                else:
                    safe_ctx[k] = str(val)
            d["ctx"] = safe_ctx
        inp = d.get("input")
        if isinstance(inp, BaseException):
            d["input"] = str(inp)
        out.append(d)
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Apex Keys API",
    description="API premium para sorteios de chaves Steam com carteira e Pix.",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
origins = settings.cors_origin_list()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else [],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    max_age=600,
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
# Alias para compatibilidade com frontend que ainda chama /admin/*
app.include_router(admin.router, prefix="/admin", tags=["admin"])

uploads_dir = Path("uploads")
uploads_dir.mkdir(exist_ok=True)
(uploads_dir / "avatars").mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
app.include_router(checkout.router, tags=["checkout"])
app.include_router(raffle_reservations.router, tags=["checkout"])
app.include_router(webhooks.router, tags=["webhooks"])
app.include_router(igdb.router, prefix="/igdb", tags=["igdb"])
app.include_router(rankings.router, prefix="/rankings", tags=["rankings"])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Dados de entrada inválidos",
            "errors": _json_safe_validation_errors(exc.errors()),
        },
    )


def _cors_headers_for_request(request: Request) -> dict[str, str]:
    """Adiciona CORS ao handler de exceção para que 5xx tenham header (browser precisa para ler o body)."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    allowed = settings.cors_origin_list()
    if not allowed:
        return {}
    if "*" in allowed:
        return {"Access-Control-Allow-Origin": "*"}
    if origin in allowed:
        return {"Access-Control-Allow-Origin": origin}
    return {}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    cors_h = _cors_headers_for_request(request)
    if isinstance(exc, HTTPException):
        headers = dict(exc.headers) if exc.headers else {}
        headers.update(cors_h)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers or None,
        )
    logger.exception("Erro não tratado: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno. Tente novamente mais tarde."},
        headers=cors_h or None,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
