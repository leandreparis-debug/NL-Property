"""Utilitaires de dates en français, indépendants de la locale du système."""

from __future__ import annotations

from datetime import date

FRENCH_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def format_month_fr(day: date) -> str:
    """Renvoie le mois et l'année en français, avec une majuscule (ex. « Octobre 2026 »)."""
    return f"{FRENCH_MONTHS[day.month - 1].capitalize()} {day.year}"
