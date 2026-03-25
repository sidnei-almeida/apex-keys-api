"""
Redimensiona e converte avatares para WebP no upload (economiza banda e armazenamento).
Imagens enormes (ex.: 16K) são reduzidas antes de gravar.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

# Lado máximo em pixels (proporção mantida) — suficiente para UI ~128–256px em retina.
AVATAR_MAX_EDGE = 384
AVATAR_WEBP_QUALITY = 82


def image_bytes_to_webp_avatar(image_bytes: bytes) -> bytes:
    """
    Abre a imagem, corrige orientação EXIF, reduz e exporta WebP.
    Levanta ValueError se o ficheiro não for imagem válida.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")

            im.thumbnail((AVATAR_MAX_EDGE, AVATAR_MAX_EDGE), Image.Resampling.LANCZOS)

            out = BytesIO()
            im.save(
                out,
                format="WEBP",
                quality=AVATAR_WEBP_QUALITY,
                method=4,
            )
            return out.getvalue()
    except Exception as e:
        raise ValueError("Não foi possível processar a imagem. Use JPG, PNG ou WebP válidos.") from e
