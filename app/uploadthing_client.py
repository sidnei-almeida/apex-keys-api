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


def get_uploadthing_api_key_from_env() -> str | None:
    token = os.getenv("UPLOADTHING_TOKEN", "").strip()
    if not token:
        return None
    obj = _decode_uploadthing_token(token)
    api_key = obj.get("apiKey")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("UPLOADTHING_TOKEN sem apiKey")
    return api_key.strip()


async def upload_bytes_to_uploadthing(
    *,
    api_key: str,
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
    headers = {"X-Uploadthing-Api-Key": api_key, "Content-Type": "application/json"}
    payload = {
        "contentDisposition": "inline",
        "acl": "public-read",
        "files": [{"name": filename, "size": len(data), "type": content_type}],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        pres = await client.post("https://api.uploadthing.com/v6/uploadFiles", headers=headers, json=payload)
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

