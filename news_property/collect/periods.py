"""Mois de newsletter et fuseau Europe/Paris."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from news_property.models import validate_month

PARIS = ZoneInfo("Europe/Paris")


def month_of(moment: datetime) -> str:
    """Mois AAAA-MM d'un instant, vu de Paris (un instant sans fuseau est considéré UTC)."""
    if moment.tzinfo is None:
        raise ValueError("Date sans fuseau horaire.")
    return moment.astimezone(PARIS).strftime("%Y-%m")


def in_month(moment: datetime, month: str) -> bool:
    """Indique si l'instant tombe dans le mois AAAA-MM, heure de Paris."""
    return month_of(moment) == validate_month(month)


def previous_month(now: datetime) -> str:
    """Mois précédant celui de now (heure de Paris), au format AAAA-MM."""
    local = now.astimezone(PARIS)
    year, month = (local.year, local.month - 1) if local.month > 1 else (local.year - 1, 12)
    return f"{year:04d}-{month:02d}"
