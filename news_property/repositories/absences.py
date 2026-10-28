"""Dépôt des congés et absences de l'équipe (rubrique 5)."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Sequence

from news_property.db.connection import transaction
from news_property.models import Absence
from news_property.repositories import _common
from news_property.repositories._common import from_iso_date, to_iso_date

_COLUMNS = "id, newsletter_id, person_name, start_date, end_date, reason, position"
_SCOPE = "newsletter_id = ?"


def _from_row(row: sqlite3.Row) -> Absence:
    """Ligne SQL → Absence."""
    start = from_iso_date(row["start_date"])
    assert start is not None
    return Absence(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        person_name=row["person_name"],
        start_date=start,
        end_date=from_iso_date(row["end_date"]),
        reason=row["reason"],
        position=row["position"],
    )


def get(conn: sqlite3.Connection, absence_id: int) -> Absence | None:
    """Absence par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM absences WHERE id = ?", (absence_id,)).fetchone()
    return _from_row(row) if row else None


def add(conn: sqlite3.Connection, absence: Absence) -> Absence:
    """Ajoute une absence (en fin de liste si position est None)."""
    item = dataclasses.replace(absence)
    with transaction(conn):
        if item.position is None:
            item.position = _common.next_position(conn, "absences", _SCOPE, (item.newsletter_id,))
        cursor = conn.execute(
            "INSERT INTO absences (newsletter_id, person_name, start_date, end_date, reason,"
            " position) VALUES (?, ?, ?, ?, ?, ?)",
            (
                item.newsletter_id,
                item.person_name,
                to_iso_date(item.start_date),
                to_iso_date(item.end_date),
                item.reason,
                item.position,
            ),
        )
        item.id = cursor.lastrowid
    return item


def list_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> list[Absence]:
    """Absences d'une newsletter, dans l'ordre d'affichage."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM absences WHERE {_SCOPE} ORDER BY position, id", (newsletter_id,)
    )
    return [_from_row(row) for row in rows]


def update(conn: sqlite3.Connection, absence: Absence) -> Absence:
    """Enregistre les modifications d'une absence (LookupError si elle n'existe pas)."""
    absence_id = _common.require_id(absence.id, "Absence")
    item = dataclasses.replace(absence)
    cursor = conn.execute(
        "UPDATE absences SET person_name = ?, start_date = ?, end_date = ?, reason = ?,"
        " position = COALESCE(?, position) WHERE id = ?",
        (
            item.person_name,
            to_iso_date(item.start_date),
            to_iso_date(item.end_date),
            item.reason,
            item.position,
            absence_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Absence introuvable (id {absence_id}).")
    updated = get(conn, absence_id)
    assert updated is not None
    return updated


def reorder(conn: sqlite3.Connection, newsletter_id: int, ordered_ids: Sequence[int]) -> None:
    """Fixe l'ordre d'affichage des absences (celles non citées suivent)."""
    _common.reorder(conn, "absences", _SCOPE, (newsletter_id,), ordered_ids)


def sort_by_date(conn: sqlite3.Connection, newsletter_id: int) -> None:
    """Classe les absences par date de début."""
    items = sorted(
        list_for_newsletter(conn, newsletter_id), key=lambda a: (a.start_date, a.position)
    )
    reorder(conn, newsletter_id, [a.id for a in items])  # type: ignore[misc]


def delete(conn: sqlite3.Connection, absence_id: int) -> None:
    """Supprime une absence."""
    conn.execute("DELETE FROM absences WHERE id = ?", (absence_id,))
