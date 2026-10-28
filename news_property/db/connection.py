"""Connexion SQLite, migrations versionnées et intégration à Flask (une connexion par requête)."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, current_app, g

from news_property.config import Settings

logger = logging.getLogger(__name__)

DB_FILE_NAME = "news_property.sqlite3"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
BUSY_TIMEOUT_MS = 5000


class DatabaseError(RuntimeError):
    """Erreur liée à la base de données locale (message en français)."""


class MigrationError(DatabaseError):
    """Une migration n'a pas pu être appliquée ; la base est restée dans son état précédent."""


@dataclass(frozen=True)
class Migration:
    """Fichier de migration SQL numéroté (ex. 0001_initial.sql)."""

    version: int
    name: str
    path: Path


def db_path(settings: Settings) -> Path:
    """Chemin du fichier de base : <données>/db/news_property.sqlite3."""
    return settings.db_dir / DB_FILE_NAME


def get_connection(settings: Settings) -> sqlite3.Connection:
    """Ouvre une connexion configurée sur la base locale.

    - row_factory = sqlite3.Row (accès aux colonnes par nom) ;
    - PRAGMA foreign_keys = ON, journal_mode = WAL, busy_timeout ;
    - mode autocommit : chaque instruction est validée immédiatement, et les opérations
      en plusieurs instructions utilisent transaction().
    """
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS:d}")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Regroupe plusieurs instructions dans une transaction (validée ou annulée en bloc).

    Si une transaction est déjà ouverte, le bloc s'y intègre (pas d'imbrication).
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def list_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Liste les migrations disponibles, triées par numéro.

    Lève MigrationError si un fichier .sql est mal nommé ou si un numéro est en double.
    """
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f"Fichier de migration mal nommé : {path.name} "
                "(attendu : 0001_description.sql, en minuscules)."
            )
        migrations.append(Migration(version=int(match.group(1)), name=path.name, path=path))
    versions = [m.version for m in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f"Numéros de migration en double : {duplicates}.")
    return migrations


def current_version(conn: sqlite3.Connection) -> int:
    """Version du schéma appliquée (0 pour une base vierge)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0])


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[int]:
    """Applique, dans l'ordre, les migrations plus récentes que la version de la base.

    Chaque migration est appliquée dans sa propre transaction : en cas d'échec, elle est
    entièrement annulée et MigrationError est levée. Renvoie les versions appliquées.
    """
    migrations = list_migrations(migrations_dir)
    version = current_version(conn)
    known = max((m.version for m in migrations), default=0)
    if version > known:
        raise MigrationError(
            f"La base de données est en version {version}, plus récente que l'application "
            f"(version {known}). Mettez l'application à jour."
        )
    applied: list[int] = []
    for migration in migrations:
        if migration.version <= version:
            continue
        sql = migration.path.read_text(encoding="utf-8")
        try:
            # executescript exécute le script tel quel : la transaction ouverte par BEGIN
            # couvre toutes ses instructions, puis l'enregistrement de la version.
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\n;")
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            logger.exception("Échec de la migration %s", migration.name)
            raise MigrationError(
                f"La migration {migration.name} a échoué : {exc}. "
                f"La base est restée en version {version}."
            ) from exc
        logger.info("Migration appliquée : %s", migration.name)
        version = migration.version
        applied.append(version)
    return applied


def init_db(settings: Settings, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Crée la base si besoin et applique les migrations manquantes ; renvoie la version."""
    with closing(get_connection(settings)) as conn:
        apply_migrations(conn, migrations_dir)
        return current_version(conn)


# --------------------------------------------------------------------------- Flask


def get_db() -> sqlite3.Connection:
    """Connexion de la requête Flask en cours (ouverte au premier appel, fermée en fin
    de requête)."""
    if "db" not in g:
        g.db = get_connection(current_app.config["CP_SETTINGS"])
    return g.db


def close_db(exc: BaseException | None = None) -> None:
    """Ferme la connexion de la requête en cours, s'il y en a une."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app: Flask) -> None:
    """Branche la fermeture automatique de la connexion en fin de requête."""
    app.teardown_appcontext(close_db)
