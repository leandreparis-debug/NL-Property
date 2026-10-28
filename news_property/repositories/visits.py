"""Dépôt des visites d'entrepôts (rubrique 3) et de leurs photos."""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from news_property.db.connection import transaction
from news_property.models import Visit, VisitPhoto
from news_property.repositories import _common
from news_property.repositories._common import from_iso_date, to_iso_date

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id, newsletter_id, site_name, location, visit_date, owner_name, surface_sqm, description,"
    " position"
)
_PHOTO_COLUMNS = "id, visit_id, file_name, caption, position"
_SCOPE = "newsletter_id = ?"
_PHOTO_SCOPE = "visit_id = ?"


def _from_row(row: sqlite3.Row) -> Visit:
    """Ligne SQL → Visit."""
    return Visit(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        site_name=row["site_name"],
        location=row["location"],
        visit_date=from_iso_date(row["visit_date"]),
        owner_name=row["owner_name"],
        surface_sqm=row["surface_sqm"],
        description=row["description"],
        position=row["position"],
    )


def _photo_from_row(row: sqlite3.Row) -> VisitPhoto:
    """Ligne SQL → VisitPhoto."""
    return VisitPhoto(
        id=row["id"],
        visit_id=row["visit_id"],
        file_name=row["file_name"],
        caption=row["caption"],
        position=row["position"],
    )


def get(conn: sqlite3.Connection, visit_id: int) -> Visit | None:
    """Visite par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM visits WHERE id = ?", (visit_id,)).fetchone()
    return _from_row(row) if row else None


def add(conn: sqlite3.Connection, visit: Visit) -> Visit:
    """Ajoute une visite (en fin de liste si position est None) et la renvoie avec son id."""
    item = dataclasses.replace(visit)
    with transaction(conn):
        if item.position is None:
            item.position = _common.next_position(conn, "visits", _SCOPE, (item.newsletter_id,))
        cursor = conn.execute(
            "INSERT INTO visits (newsletter_id, site_name, location, visit_date, owner_name,"
            " surface_sqm, description, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.newsletter_id,
                item.site_name,
                item.location,
                to_iso_date(item.visit_date),
                item.owner_name,
                item.surface_sqm,
                item.description,
                item.position,
            ),
        )
        item.id = cursor.lastrowid
    return item


def list_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> list[Visit]:
    """Visites d'une newsletter, dans l'ordre d'affichage."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM visits WHERE {_SCOPE} ORDER BY position, id", (newsletter_id,)
    )
    return [_from_row(row) for row in rows]


def update(conn: sqlite3.Connection, visit: Visit) -> Visit:
    """Enregistre les modifications d'une visite (LookupError si elle n'existe pas)."""
    visit_id = _common.require_id(visit.id, "Visite")
    item = dataclasses.replace(visit)
    cursor = conn.execute(
        "UPDATE visits SET site_name = ?, location = ?, visit_date = ?, owner_name = ?,"
        " surface_sqm = ?, description = ?, position = COALESCE(?, position) WHERE id = ?",
        (
            item.site_name,
            item.location,
            to_iso_date(item.visit_date),
            item.owner_name,
            item.surface_sqm,
            item.description,
            item.position,
            visit_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Visite introuvable (id {visit_id}).")
    updated = get(conn, visit_id)
    assert updated is not None
    return updated


def reorder(conn: sqlite3.Connection, newsletter_id: int, ordered_ids: Sequence[int]) -> None:
    """Fixe l'ordre d'affichage des visites (celles non citées suivent)."""
    _common.reorder(conn, "visits", _SCOPE, (newsletter_id,), ordered_ids)


def delete(conn: sqlite3.Connection, visit_id: int, photos_dir: Path | None = None) -> None:
    """Supprime une visite et ses photos (lignes en cascade ; fichiers aussi si photos_dir
    est donné)."""
    photos = list_photos(conn, visit_id)
    conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
    if photos_dir is not None:
        for photo in photos:
            remove_photo_file(photos_dir, photo.file_name)


# --------------------------------------------------------------------------- photos


def add_photo(conn: sqlite3.Connection, photo: VisitPhoto) -> VisitPhoto:
    """Enregistre une photo de visite (fichier déjà copié dans le dossier photos).

    file_name est relatif au dossier photos ; ValueError si c'est un chemin absolu.
    """
    item = dataclasses.replace(photo)
    with transaction(conn):
        if item.position is None:
            item.position = _common.next_position(
                conn, "visit_photos", _PHOTO_SCOPE, (item.visit_id,)
            )
        cursor = conn.execute(
            "INSERT INTO visit_photos (visit_id, file_name, caption, position) VALUES (?, ?, ?, ?)",
            (item.visit_id, item.file_name, item.caption, item.position),
        )
        item.id = cursor.lastrowid
    return item


def get_photo(conn: sqlite3.Connection, photo_id: int) -> VisitPhoto | None:
    """Photo par identifiant, ou None."""
    row = conn.execute(
        f"SELECT {_PHOTO_COLUMNS} FROM visit_photos WHERE id = ?", (photo_id,)
    ).fetchone()
    return _photo_from_row(row) if row else None


def list_photos(conn: sqlite3.Connection, visit_id: int) -> list[VisitPhoto]:
    """Photos d'une visite, dans l'ordre d'affichage."""
    rows = conn.execute(
        f"SELECT {_PHOTO_COLUMNS} FROM visit_photos WHERE {_PHOTO_SCOPE} ORDER BY position, id",
        (visit_id,),
    )
    return [_photo_from_row(row) for row in rows]


def reorder_photos(conn: sqlite3.Connection, visit_id: int, ordered_ids: Sequence[int]) -> None:
    """Fixe l'ordre des photos d'une visite (celles non citées suivent)."""
    _common.reorder(conn, "visit_photos", _PHOTO_SCOPE, (visit_id,), ordered_ids)


def set_caption(conn: sqlite3.Connection, photo_id: int, caption: str | None) -> None:
    """Enregistre la légende d'une photo (vide autorisé)."""
    conn.execute("UPDATE visit_photos SET caption = ? WHERE id = ?", (caption, photo_id))


def delete_photo(conn: sqlite3.Connection, photo_id: int, photos_dir: Path) -> None:
    """Supprime une photo : la ligne en base et le fichier dans photos_dir.

    Aucune erreur si le fichier a déjà disparu.
    """
    photo = get_photo(conn, photo_id)
    if photo is None:
        return
    conn.execute("DELETE FROM visit_photos WHERE id = ?", (photo_id,))
    remove_photo_file(photos_dir, photo.file_name)


def remove_photo_file(photos_dir: Path, file_name: str) -> None:
    """Supprime un fichier photo s'il existe, sans jamais sortir du dossier photos."""
    root = photos_dir.resolve()
    path = (root / file_name).resolve()
    if not path.is_relative_to(root) or path == root:
        logger.warning("Suppression refusée hors du dossier photos : %s", file_name)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Impossible de supprimer la photo %s", path, exc_info=True)
