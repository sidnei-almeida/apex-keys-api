"""Carrega variáveis de `.env` na raiz do projeto sem sobrescrever o ambiente já definido."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ENV = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or _DEFAULT_ENV
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
