"""Tests de la connexion et des migrations."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from news_property.config import Settings
from news_property.db.connection import (
    MIGRATIONS_DIR,
    MigrationError,
    current_version,
    db_path,
    get_connection,
    init_db,
    list_migrations,
    transaction,
)
from tests.helpers import FIXTURES_DIR

LATEST = list_migrations()[-1].version

EXPECTED_TABLES = {
    "schema_version",
    "newsletters",
    "actors",
    "articles",
    "collection_runs",
    "visits",
    "visit_photos",
    "events",
    "absences",
    "document_links",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}


def test_fresh_database(settings: Settings) -> None:
    assert init_db(settings) == LATEST
    assert db_path(settings) == settings.data_dir / "db" / "news_property.sqlite3"
    assert db_path(settings).is_file()
    with closing(get_connection(settings)) as conn:
        assert EXPECTED_TABLES <= _tables(conn)
        assert current_version(conn) == LATEST
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert isinstance(conn.execute("SELECT 1 AS x").fetchone(), sqlite3.Row)


def test_init_db_twice_does_nothing(settings: Settings) -> None:
    init_db(settings)
    with closing(get_connection(settings)) as conn:
        before = conn.execute("SELECT version, applied_at FROM schema_version").fetchall()
    assert init_db(settings) == LATEST
    with closing(get_connection(settings)) as conn:
        after = conn.execute("SELECT version, applied_at FROM schema_version").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_failed_migration_leaves_database_intact(settings: Settings, tmp_path: Path) -> None:
    init_db(settings)
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for migration in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(migration, migrations)
    shutil.copy(FIXTURES_DIR / "migrations_broken" / "0099_broken.sql", migrations)

    with pytest.raises(MigrationError, match="0099_broken.sql a échoué") as excinfo:
        init_db(settings, migrations)
    assert f"version {LATEST}" in str(excinfo.value)

    with closing(get_connection(settings)) as conn:
        assert current_version(conn) == LATEST
        assert "should_not_exist" not in _tables(conn)
        assert "extra" not in _columns(conn, "articles")


def test_failed_first_migration_on_fresh_database(settings: Settings, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    shutil.copy(FIXTURES_DIR / "migrations_broken" / "0099_broken.sql", migrations / "0001_x.sql")
    with pytest.raises(MigrationError):
        init_db(settings, migrations)
    with closing(get_connection(settings)) as conn:
        assert _tables(conn) == {"schema_version"}
        assert current_version(conn) == 0


def test_database_newer_than_application(settings: Settings, tmp_path: Path) -> None:
    init_db(settings)
    with pytest.raises(MigrationError, match="plus récente"):
        init_db(settings, tmp_path)  # dossier sans migration : version connue 0


def test_badly_named_migration(tmp_path: Path) -> None:
    (tmp_path / "1_init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="mal nommé"):
        list_migrations(tmp_path)


def test_duplicate_migration_number(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="en double"):
        list_migrations(tmp_path)


def test_transaction_rolls_back(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute(
            "INSERT INTO newsletters (month, created_at, updated_at) VALUES (?, ?, ?)",
            ("2026-01", "x", "x"),
        )
        raise RuntimeError("annulation")
    assert conn.execute("SELECT COUNT(*) FROM newsletters").fetchone()[0] == 0


def test_migration_0002_keeps_step_2_data(settings: Settings, tmp_path: Path) -> None:
    """Une base de l'étape 2 (schéma v1, avec données) passe en dernière version sans perte."""
    v1 = tmp_path / "migrations_v1"
    v1.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", v1)
    assert init_db(settings, v1) == 1
    with closing(get_connection(settings)) as conn:
        conn.execute(
            "INSERT INTO newsletters (id, month, created_at, updated_at)"
            " VALUES (1, '2026-09', 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO articles (newsletter_id, rubric, title, source_name, url, selected,"
            " position, collected_at) VALUES (1, 1, 'Titre relu', 'Source',"
            " 'https://a.example/1', 0, 4, '2026-09-30T10:00:00+00:00')"
        )

    assert init_db(settings) == LATEST

    with closing(get_connection(settings)) as conn:
        row = conn.execute("SELECT * FROM articles").fetchone()
        assert (row["title"], row["selected"], row["position"]) == ("Titre relu", 0, 4)
        assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 0
        # Migration 0003 : la rubrique 3 (réglementation) est acceptée, pas la 4 ; les index
        # de la table articles ont été refaits.
        conn.execute(
            "INSERT INTO articles (newsletter_id, rubric, title, source_name, url, collected_at)"
            " VALUES (1, 3, 'Décret', 'Source', 'https://a.example/3', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO articles (newsletter_id, rubric, title, source_name, url,"
                " collected_at) VALUES (1, 4, 'Titre', 'Source', 'https://a.example/4', 'x')"
            )
        indexes = {r[1] for r in conn.execute("PRAGMA index_list('articles')").fetchall()}
        assert {"idx_articles_rubric", "idx_articles_actor", "idx_articles_transaction"} <= indexes
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO document_links (newsletter_id, position, url)"
                " VALUES (1, 7, 'https://drive.example/x')"
            )
