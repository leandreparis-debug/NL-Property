"""Dépôt des liens vers les documents utiles (rubrique 6, jusqu'à 6 par newsletter)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from news_property.db.connection import transaction
from news_property.models import MAX_DOCUMENT_LINKS, DocumentLink

_COLUMNS = "id, newsletter_id, position, label, url"


def _from_row(row: sqlite3.Row) -> DocumentLink:
    """Ligne SQL → DocumentLink."""
    return DocumentLink(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        position=row["position"],
        label=row["label"],
        url=row["url"],
    )


def list_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> list[DocumentLink]:
    """Liens de la newsletter, dans l'ordre (position 1 à 6)."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM document_links WHERE newsletter_id = ? ORDER BY position",
        (newsletter_id,),
    ).fetchall()
    return [_from_row(row) for row in rows]


def replace_all(
    conn: sqlite3.Connection, newsletter_id: int, links: Sequence[DocumentLink]
) -> list[DocumentLink]:
    """Remplace tous les liens de la newsletter (liste vide = tout retirer)."""
    if len(links) > MAX_DOCUMENT_LINKS:
        raise ValueError(f"{MAX_DOCUMENT_LINKS} liens au plus.")
    if any(link.newsletter_id != newsletter_id for link in links):
        raise ValueError("Lien rattaché à une autre newsletter.")
    with transaction(conn):
        conn.execute("DELETE FROM document_links WHERE newsletter_id = ?", (newsletter_id,))
        for link in links:
            conn.execute(
                "INSERT INTO document_links (newsletter_id, position, label, url)"
                " VALUES (?, ?, ?, ?)",
                (newsletter_id, link.position, link.label, link.url),
            )
    return list_for_newsletter(conn, newsletter_id)


def count(conn: sqlite3.Connection, newsletter_id: int) -> int:
    """Nombre de liens de la newsletter."""
    return conn.execute(
        "SELECT COUNT(*) FROM document_links WHERE newsletter_id = ?", (newsletter_id,)
    ).fetchone()[0]
