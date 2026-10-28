"""Dépôt des exécutions de la collecte de veille."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from news_property.models import RUN_FINAL_STATUSES, CollectionRun
from news_property.repositories._common import (
    from_iso_datetime,
    to_iso_datetime,
    utcnow,
)

_COLUMNS = "id, newsletter_id, started_at, finished_at, status, stats, errors"


def _from_row(row: sqlite3.Row) -> CollectionRun:
    """Ligne SQL → CollectionRun."""
    started_at = from_iso_datetime(row["started_at"])
    assert started_at is not None
    return CollectionRun(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        started_at=started_at,
        finished_at=from_iso_datetime(row["finished_at"]),
        status=row["status"],
        stats=json.loads(row["stats"]),
        errors=json.loads(row["errors"]),
    )


def get(conn: sqlite3.Connection, run_id: int) -> CollectionRun | None:
    """Collecte par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM collection_runs WHERE id = ?", (run_id,)).fetchone()
    return _from_row(row) if row else None


def start(
    conn: sqlite3.Connection, newsletter_id: int, now: datetime | None = None
) -> CollectionRun:
    """Enregistre le début d'une collecte (statut running) et la renvoie.

    now : heure de début (injectée par le collecteur) ; par défaut, maintenant.
    """
    run = CollectionRun(newsletter_id=newsletter_id, started_at=now or utcnow())
    cursor = conn.execute(
        "INSERT INTO collection_runs (newsletter_id, started_at, status) VALUES (?, ?, ?)",
        (run.newsletter_id, to_iso_datetime(run.started_at), run.status),
    )
    run.id = cursor.lastrowid
    return run


def finish(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    stats: dict[str, Any] | None = None,
    errors: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CollectionRun:
    """Clôt une collecte avec son statut final (success, partial, failed), ses statistiques
    et ses erreurs (phrases en français). now : heure de fin, par défaut maintenant."""
    if status not in RUN_FINAL_STATUSES:
        raise ValueError(
            f"Statut final de collecte invalide : {status!r} "
            f"(attendu : {', '.join(RUN_FINAL_STATUSES)})."
        )
    cursor = conn.execute(
        "UPDATE collection_runs SET status = ?, finished_at = ?, stats = ?, errors = ?"
        " WHERE id = ?",
        (
            status,
            to_iso_datetime(now or utcnow()),
            json.dumps(stats or {}, ensure_ascii=False),
            json.dumps(list(errors or []), ensure_ascii=False),
            run_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Collecte introuvable (id {run_id}).")
    run = get(conn, run_id)
    assert run is not None
    return run


def last_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> CollectionRun | None:
    """Dernière collecte lancée pour une newsletter, ou None."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM collection_runs WHERE newsletter_id = ?"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (newsletter_id,),
    ).fetchone()
    return _from_row(row) if row else None


def list_running(conn: sqlite3.Connection) -> list[CollectionRun]:
    """Collectes encore marquées « running » (toutes newsletters confondues)."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM collection_runs WHERE status = 'running' ORDER BY started_at"
    )
    return [_from_row(row) for row in rows]
