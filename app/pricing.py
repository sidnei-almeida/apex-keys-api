from decimal import ROUND_HALF_UP, Decimal

_TWO_DP = Decimal("0.01")


def tactical_ticket_price(total_price: Decimal, total_tickets: int) -> Decimal:
    """
    Preço por bilhete = (total_price / total_tickets) arredondado a **duas casas decimais**
    com regra **half-up** (0,5 para cima nas milésimas).

    Isto evita o comportamento de **teto** (ceil), em que valores como 399,991… viravam 400,00.
    Nota: ao contrário do ceil, a soma `ticket_price * total_tickets` pode ficar ligeiramente
    abaixo de `total_price` quando a divisão não é exata (ex.: 100 ÷ 3 → 33,33 × 3 = 99,99).
    """
    if total_tickets <= 0:
        raise ValueError("total_tickets deve ser positivo")
    if total_price <= 0:
        raise ValueError("total_price deve ser positivo")
    per = total_price / Decimal(total_tickets)
    return per.quantize(_TWO_DP, rounding=ROUND_HALF_UP)
