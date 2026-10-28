"""Dépôt des newsletters (une par mois)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from news_property.db.connection import transaction
from news_property.models import (
    NEWSLETTER_STATUSES,
    FrontPageFigures,
    Newsletter,
    validate_month,
)
from news_property.repositories import visits
from news_property.repositories._common import from_iso_datetime, to_iso_datetime, utcnow

_COLUMNS = "id, month, status, created_at, updated_at"


def _from_row(row: sqlite3.Row) -> Newsletter:
    """Ligne SQL → Newsletter."""
    return Newsletter(
        id=row["id"],
        month=row["month"],
        status=row["status"],
        created_at=from_iso_datetime(row["created_at"]),
        updated_at=from_iso_datetime(row["updated_at"]),
    )


def get(conn: sqlite3.Connection, month: str) -> Newsletter | None:
    """Newsletter du mois AAAA-MM, ou None si elle n'existe pas. ValueError si mois invalide."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM newsletters WHERE month = ?", (validate_month(month),)
    ).fetchone()
    return _from_row(row) if row else None


def get_by_id(conn: sqlite3.Connection, newsletter_id: int) -> Newsletter | None:
    """Newsletter par identifiant, ou None."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM newsletters WHERE id = ?", (newsletter_id,)
    ).fetchone()
    return _from_row(row) if row else None


def get_or_create(conn: sqlite3.Connection, month: str) -> Newsletter:
    """Renvoie la newsletter du mois, en la créant (statut draft) si besoin. Idempotent."""
    month = validate_month(month)
    now = to_iso_datetime(utcnow())
    conn.execute(
        "INSERT INTO newsletters (month, created_at, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (month) DO NOTHING",
        (month, now, now),
    )
    newsletter = get(conn, month)
    assert newsletter is not None
    return newsletter


def list_all(conn: sqlite3.Connection) -> list[Newsletter]:
    """Historique des newsletters, de la plus récente à la plus ancienne."""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM newsletters ORDER BY month DESC")
    return [_from_row(row) for row in rows]


def set_status(conn: sqlite3.Connection, newsletter_id: int, status: str) -> Newsletter:
    """Change le statut (draft, reviewed, exported). ValueError si statut inconnu."""
    if status not in NEWSLETTER_STATUSES:
        raise ValueError(
            f"Statut de newsletter invalide : {status!r} "
            f"(attendu : {', '.join(NEWSLETTER_STATUSES)})."
        )
    cursor = conn.execute(
        "UPDATE newsletters SET status = ?, updated_at = ? WHERE id = ?",
        (status, to_iso_datetime(utcnow()), newsletter_id),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Newsletter introuvable (id {newsletter_id}).")
    newsletter = get_by_id(conn, newsletter_id)
    assert newsletter is not None
    return newsletter


def delete(conn: sqlite3.Connection, newsletter_id: int, photos_dir: Path | None = None) -> None:
    """Supprime une newsletter et, en cascade, tout son contenu.

    Si photos_dir est donné, les fichiers des photos de visite sont aussi supprimés.
    """
    with transaction(conn):
        file_names = [
            row["file_name"]
            for row in conn.execute(
                "SELECT p.file_name FROM visit_photos p "
                "JOIN visits v ON v.id = p.visit_id WHERE v.newsletter_id = ?",
                (newsletter_id,),
            )
        ]
        conn.execute("DELETE FROM newsletters WHERE id = ?", (newsletter_id,))
    if photos_dir is not None:
        for file_name in file_names:
            visits.remove_photo_file(photos_dir, file_name)


def front_page_figures(conn: sqlite3.Connection, newsletter_id: int) -> FrontPageFigures:
    """Chiffres de la Une, calculés à la volée : visites d'entrepôts, événements du
    calendrier et articles de veille retenus."""
    row = conn.execute(
        "SELECT"
        " (SELECT COUNT(*) FROM visits WHERE newsletter_id = :id) AS visits,"
        " (SELECT COUNT(*) FROM events WHERE newsletter_id = :id) AS events,"
        " (SELECT COUNT(*) FROM articles WHERE newsletter_id = :id AND selected = 1) AS articles",
        {"id": newsletter_id},
    ).fetchone()
    return FrontPageFigures(visits=row["visits"], events=row["events"], articles=row["articles"])
