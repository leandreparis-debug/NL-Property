"""Outils partagés par les dépôts : conversions de dates et ordre d'affichage."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import UTC, date, datetime

from news_property.db.connection import transaction

# Tables dont l'ordre d'affichage est géré par une colonne position (noms fixés dans le
# code, jamais issus de données utilisateur).
ORDERED_TABLES = frozenset({"articles", "visits", "visit_photos", "events", "absences"})


def utcnow() -> datetime:
    """Date et heure courantes en UTC, à la seconde près."""
    return datetime.now(UTC).replace(microsecond=0)


def to_iso_datetime(value: datetime | None) -> str | None:
    """datetime → texte ISO 8601 (heure sans fuseau considérée comme UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat(timespec="seconds")


def from_iso_datetime(value: str | None) -> datetime | None:
    """Texte ISO 8601 → datetime."""
    return datetime.fromisoformat(value) if value else None


def to_iso_date(value: date | None) -> str | None:
    """date → texte AAAA-MM-JJ."""
    return value.isoformat() if value is not None else None


def from_iso_date(value: str | None) -> date | None:
    """Texte AAAA-MM-JJ → date."""
    return date.fromisoformat(value) if value else None


def require_id(item_id: int | None, label: str) -> int:
    """Vérifie qu'un objet à modifier a bien un identifiant."""
    if item_id is None:
        raise ValueError(f"{label} sans identifiant : enregistrez-le avant de le modifier.")
    return item_id


def next_position(
    conn: sqlite3.Connection, table: str, scope_sql: str, params: Sequence[object]
) -> int:
    """Position suivant la dernière de l'ensemble (scope_sql est un filtre fixé dans le code)."""
    assert table in ORDERED_TABLES
    row = conn.execute(
        f"SELECT COALESCE(MAX(position) + 1, 0) FROM {table} WHERE {scope_sql}", params
    ).fetchone()
    return int(row[0])


def reorder(
    conn: sqlite3.Connection,
    table: str,
    scope_sql: str,
    params: Sequence[object],
    ordered_ids: Sequence[int],
) -> None:
    """Renumérote les positions (0, 1, 2...) dans l'ordre donné.

    Les éléments de l'ensemble absents de ordered_ids sont placés ensuite, dans leur
    ordre actuel. Lève ValueError si un identifiant n'appartient pas à l'ensemble ou
    apparaît deux fois.
    """
    assert table in ORDERED_TABLES
    with transaction(conn):
        current = [
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM {table} WHERE {scope_sql} ORDER BY position, id", params
            )
        ]
        wanted = list(ordered_ids)
        if len(set(wanted)) != len(wanted):
            raise ValueError("Un élément apparaît deux fois dans le nouvel ordre.")
        unknown = set(wanted) - set(current)
        if unknown:
            raise ValueError(f"Éléments inconnus dans ce classement : {sorted(unknown)}.")
        final = wanted + [item_id for item_id in current if item_id not in set(wanted)]
        conn.executemany(
            f"UPDATE {table} SET position = ? WHERE id = ?",
            [(position, item_id) for position, item_id in enumerate(final)],
        )
