"""Dépôt des acteurs suivis et synchronisation avec config/acteurs.yaml."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from news_property.actor_catalog import ACTORS_FILE, load_actor_catalog, slugify
from news_property.db.connection import transaction
from news_property.models import Actor

_COLUMNS = "id, name, slug, category, aliases, tracked, case_sensitive"


@dataclass(frozen=True)
class ActorSyncResult:
    """Bilan d'une synchronisation du référentiel (noms des acteurs concernés)."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)


def _from_row(row: sqlite3.Row) -> Actor:
    """Ligne SQL → Actor."""
    return Actor(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        category=row["category"],
        aliases=json.loads(row["aliases"]),
        tracked=bool(row["tracked"]),
        case_sensitive=bool(row["case_sensitive"]),
    )


def list_all(conn: sqlite3.Connection, tracked_only: bool = False) -> list[Actor]:
    """Acteurs par ordre alphabétique ; tracked_only pour n'avoir que ceux suivis."""
    sql = f"SELECT {_COLUMNS} FROM actors"
    if tracked_only:
        sql += " WHERE tracked = 1"
    return [_from_row(row) for row in conn.execute(sql + " ORDER BY name COLLATE NOCASE")]


def get(conn: sqlite3.Connection, actor_id: int) -> Actor | None:
    """Acteur par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM actors WHERE id = ?", (actor_id,)).fetchone()
    return _from_row(row) if row else None


def get_by_name(conn: sqlite3.Connection, name: str) -> Actor | None:
    """Acteur par nom exact, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM actors WHERE name = ?", (name,)).fetchone()
    return _from_row(row) if row else None


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Actor | None:
    """Acteur par slug, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM actors WHERE slug = ?", (slug,)).fetchone()
    return _from_row(row) if row else None


def sync_from_yaml(conn: sqlite3.Connection, path: Path = ACTORS_FILE) -> ActorSyncResult:
    """Synchronise la table actors avec le fichier YAML, en une transaction.

    Ajoute les nouveaux acteurs, met à jour catégorie, alias et case_sensitive des
    existants (repérés par leur nom) et repasse tracked = 1 ; un acteur retiré du YAML
    n'est jamais supprimé : il passe tracked = 0. Lève ActorCatalogError si le
    fichier est invalide (la base n'est alors pas modifiée).
    """
    entries = load_actor_catalog(path)
    result = ActorSyncResult()
    with transaction(conn):
        existing = {actor.name: actor for actor in list_all(conn)}
        for entry in entries:
            aliases = list(entry.aliases)
            actor = existing.get(entry.name)
            if actor is None:
                conn.execute(
                    "INSERT INTO actors (name, slug, category, aliases, tracked, case_sensitive)"
                    " VALUES (?, ?, ?, ?, 1, ?)",
                    (
                        entry.name,
                        slugify(entry.name),
                        entry.category,
                        json.dumps(aliases, ensure_ascii=False),
                        int(entry.case_sensitive),
                    ),
                )
                result.added.append(entry.name)
            elif (actor.category, actor.aliases, actor.tracked, actor.case_sensitive) != (
                entry.category,
                aliases,
                True,
                entry.case_sensitive,
            ):
                conn.execute(
                    "UPDATE actors SET category = ?, aliases = ?, tracked = 1, case_sensitive = ?"
                    " WHERE id = ?",
                    (
                        entry.category,
                        json.dumps(aliases, ensure_ascii=False),
                        int(entry.case_sensitive),
                        actor.id,
                    ),
                )
                result.updated.append(entry.name)
        names = {entry.name for entry in entries}
        for name, actor in existing.items():
            if name not in names and actor.tracked:
                conn.execute("UPDATE actors SET tracked = 0 WHERE id = ?", (actor.id,))
                result.untracked.append(name)
    return result
