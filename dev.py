"""
Arranque local com uvicorn e reload.

Uso:
    python dev.py

Se a porta 8000 estiver em uso: fuser -k 8000/tcp

Ou:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os

import uvicorn

from app.dotenv_loader import load_dotenv


if __name__ == "__main__":
    load_dotenv()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload = os.environ.get("UVICORN_RELOAD", "1").lower() in ("1", "true", "yes")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
    )
