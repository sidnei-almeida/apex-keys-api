from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx


def _decode_uploadthing_token(token: str) -> dict[str, Any]:
    """
    UPLOADTHING_TOKEN é um base64 de um JSON (v7). Ex.:
    {"apiKey":"sk_live_...","appId":"...","regions":["sea1"]}
    """
    t = token.strip().strip("'").strip('"')
    try:
        raw = base64.b64decode(t).decode("utf-8")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("token JSON inválido")
        return obj  # type: ignore[return-value]
    except Exception as e:
        raise ValueError("UPLOADTHING_TOKEN inválido") from e


def get_uploadthing_credentials_from_env() -> tuple[str, str] | None:
    """
    Retorna (api_key, token_raw).
    - Preferimos `UPLOADTHING_SECRET` (legacy / v6) quando presente, pois a REST v6 usa `X-Uploadthing-Api-Key: sk_...`.
    - Caso contrário, extraímos a apiKey de `UPLOADTHING_TOKEN` (v7+ base64 JSON).
    `token_raw` é mantido apenas como fallback para stacks que esperem Authorization Bearer.
    """
    legacy = os.getenv("UPLOADTHING_SECRET", "").strip().strip("'").strip('"')
    if legacy:
        return legacy, ""
    token_raw = os.getenv("UPLOADTHING_TOKEN", "").strip()
    if not token_raw:
        return None
    obj = _decode_uploadthing_token(token_raw)
    api_key = obj.get("apiKey")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("UPLOADTHING_TOKEN sem apiKey")
    return api_key.strip(), token_raw.strip().strip("'").strip('"')


async def upload_bytes_to_uploadthing(
    *,
    api_key: str,
    token_raw: str | None = None,
    filename: str,
    content_type: str,
    data: bytes,
) -> str:
    """
    Upload server-side via REST (UTApi uploadFiles).
    1) POST https://api.uploadthing.com/v6/uploadFiles -> presigned POST
    2) POST para o bucket S3 com fields + file
    Retorna a URL pública (utfs).
    """
    def _headers(mode: str) -> dict[str, str]:
        # mode=api_key (clássico) ou bearer (fallback)
        h: dict[str, str] = {"Content-Type": "application/json"}
        if mode == "api_key":
            h["X-Uploadthing-Api-Key"] = api_key
        elif mode == "bearer" and token_raw:
            h["Authorization"] = f"Bearer {token_raw}"
        else:
            h["X-Uploadthing-Api-Key"] = api_key
        return h
    payload = {
        "contentDisposition": "inline",
        "acl": "public-read",
        "files": [{"name": filename, "size": len(data), "type": content_type}],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        pres = await client.post(
            "https://api.uploadthing.com/v6/uploadFiles",
            headers=_headers("api_key"),
            json=payload,
        )
        # Alguns ambientes podem exigir token Bearer em vez da API key no header.
        if pres.status_code in (400, 401, 403) and token_raw:
            pres = await client.post(
                "https://api.uploadthing.com/v6/uploadFiles",
                headers=_headers("bearer"),
                json=payload,
            )
        pres.raise_for_status()
        j = pres.json()
        item = (j.get("data") or [None])[0]
        if not isinstance(item, dict):
            raise RuntimeError("UploadThing: resposta inesperada (presign)")

        upload_url = item.get("url")
        fields = item.get("fields")
        file_url = item.get("fileUrl") or item.get("appUrl") or item.get("url")
        if not isinstance(upload_url, str) or not isinstance(fields, dict) or not isinstance(file_url, str):
            raise RuntimeError("UploadThing: presign incompleto")

        files = {"file": (filename, data, content_type)}
        up = await client.post(upload_url, data=fields, files=files)
        up.raise_for_status()
        return file_url

