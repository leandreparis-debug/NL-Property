"""Dépôt des événements du calendrier à venir (rubrique 4)."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Sequence

from news_property.db.connection import transaction
from news_property.models import Event
from news_property.repositories import _common
from news_property.repositories._common import from_iso_date, to_iso_date

_COLUMNS = "id, newsletter_id, title, event_date, end_date, location, summary, position"
_SCOPE = "newsletter_id = ?"


def _from_row(row: sqlite3.Row) -> Event:
    """Ligne SQL → Event."""
    return Event(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        title=row["title"],
        event_date=from_iso_date(row["event_date"]),
        end_date=from_iso_date(row["end_date"]),
        location=row["location"],
        summary=row["summary"],
        position=row["position"],
    )


def get(conn: sqlite3.Connection, event_id: int) -> Event | None:
    """Événement par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM events WHERE id = ?", (event_id,)).fetchone()
    return _from_row(row) if row else None


def add(conn: sqlite3.Connection, event: Event) -> Event:
    """Ajoute un événement (en fin de liste si position est None)."""
    item = dataclasses.replace(event)
    with transaction(conn):
        if item.position is None:
            item.position = _common.next_position(conn, "events", _SCOPE, (item.newsletter_id,))
        cursor = conn.execute(
            "INSERT INTO events (newsletter_id, title, event_date, end_date, location, summary,"
            " position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item.newsletter_id,
                item.title,
                to_iso_date(item.event_date),
                to_iso_date(item.end_date),
                item.location,
                item.summary,
                item.position,
            ),
        )
        item.id = cursor.lastrowid
    return item


def list_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> list[Event]:
    """Événements d'une newsletter, dans l'ordre d'affichage."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM events WHERE {_SCOPE} ORDER BY position, id", (newsletter_id,)
    )
    return [_from_row(row) for row in rows]


def update(conn: sqlite3.Connection, event: Event) -> Event:
    """Enregistre les modifications d'un événement (LookupError s'il n'existe pas)."""
    event_id = _common.require_id(event.id, "Événement")
    item = dataclasses.replace(event)
    cursor = conn.execute(
        "UPDATE events SET title = ?, event_date = ?, end_date = ?, location = ?, summary = ?,"
        " position = COALESCE(?, position) WHERE id = ?",
        (
            item.title,
            to_iso_date(item.event_date),
            to_iso_date(item.end_date),
            item.location,
            item.summary,
            item.position,
            event_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Événement introuvable (id {event_id}).")
    updated = get(conn, event_id)
    assert updated is not None
    return updated


def reorder(conn: sqlite3.Connection, newsletter_id: int, ordered_ids: Sequence[int]) -> None:
    """Fixe l'ordre d'affichage des événements (ceux non cités suivent)."""
    _common.reorder(conn, "events", _SCOPE, (newsletter_id,), ordered_ids)


def sort_by_date(conn: sqlite3.Connection, newsletter_id: int) -> None:
    """Classe les événements par date (les sans date à la fin, dans leur ordre actuel)."""
    items = list_for_newsletter(conn, newsletter_id)
    dated = sorted((e for e in items if e.event_date), key=lambda e: (e.event_date, e.position))
    undated = [e for e in items if not e.event_date]
    reorder(conn, newsletter_id, [e.id for e in dated + undated])  # type: ignore[misc]


def delete(conn: sqlite3.Connection, event_id: int) -> None:
    """Supprime un événement."""
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
