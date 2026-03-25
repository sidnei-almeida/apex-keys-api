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
AVATAR_MAX_BYTES = 50 * 1024  # 50KB (limite pós-pipeline)


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


def image_bytes_to_webp_avatar_under_limit(
    image_bytes: bytes,
    *,
    max_bytes: int = AVATAR_MAX_BYTES,
) -> bytes:
    """
    Converte para WebP e tenta garantir um tamanho final <= max_bytes.
    Estratégia: reduzir qualidade e, se necessário, reduzir dimensão máxima.
    """
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")

            # Tentativas: primeiro mantém 384px; se não couber, reduz aresta.
            for edge in (AVATAR_MAX_EDGE, 320, 256, 224, 192, 160, 128):
                tmp = im.copy()
                tmp.thumbnail((edge, edge), Image.Resampling.LANCZOS)

                # Qualidade decrescente; método 4 dá bom custo/qualidade.
                for q in (82, 76, 70, 64, 58, 52, 46, 40, 34, 28):
                    out = BytesIO()
                    tmp.save(out, format="WEBP", quality=q, method=4)
                    b = out.getvalue()
                    if len(b) <= max_bytes:
                        return b

            raise ValueError("Não foi possível otimizar a imagem para ≤ 50KB. Use uma foto mais simples.")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("Não foi possível processar a imagem. Use JPG, PNG ou WebP válidos.") from e
