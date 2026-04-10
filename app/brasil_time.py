"""Fuso e formatação para exibição ao utilizador (produto orientado ao Brasil)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")


def to_brasilia(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BRASILIA_TZ)


def format_brasilia_hm(dt: datetime) -> str:
    """Hora local de Brasília, 24h (ex.: 14:30)."""
    return to_brasilia(dt).strftime("%H:%M")
