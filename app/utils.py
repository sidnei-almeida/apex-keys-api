"""Utilitários auxiliares (ex.: dados mock para fluxo Pix de teste)."""

from decimal import Decimal


def mock_pix_qr_payload(
    gateway_reference: str,
    amount: Decimal,
    *,
    merchant_name: str = "Apex Keys (teste)",
) -> dict[str, str]:
    """
    Retorna um dict com campos típicos de QR estático/dinâmico para desenvolvimento.
    Não gera imagem real; o front pode usar libs de QR a partir de `emv_payload`.
    """
    amount_str = f"{amount:.2f}"
    return {
        "gateway_reference": gateway_reference,
        "amount_brl": amount_str,
        "merchant_name": merchant_name,
        "emv_payload": f"00020126580014br.gov.bcb.pix0136{gateway_reference}52040000530398654{amount_str}5802BR5925{merchant_name[:25]}6009SAO PAULO62070503***6304ABCD",
        "note": "Payload EMV fictício apenas para desenvolvimento local.",
    }
