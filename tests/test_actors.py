"""Tests du référentiel des acteurs suivis."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from news_property.actor_catalog import (
    ACTORS_FILE,
    ActorCatalogError,
    load_actor_catalog,
    parse_actor_catalog,
    slugify,
)
from news_property.config import ConfigError
from news_property.repositories import actors

CATEGORIES = {"Investisseur et développeur", "Logisticien", "Chargeur et distributeur", "Conseil"}


def test_project_catalog_is_valid() -> None:
    entries = load_actor_catalog(ACTORS_FILE)
    assert len(entries) >= 30
    assert {entry.category for entry in entries} == CATEGORIES
    assert all(entry.line > 0 for entry in entries)


def test_sync_creates_the_catalog(conn: sqlite3.Connection) -> None:
    result = actors.sync_from_yaml(conn, ACTORS_FILE)
    entries = load_actor_catalog(ACTORS_FILE)
    assert len(result.added) == len(entries)
    all_actors = actors.list_all(conn)
    assert len(all_actors) == len(entries) and all(a.tracked for a in all_actors)
    kn = actors.get_by_name(conn, "Kuehne+Nagel")
    assert kn is not None
    assert kn.slug == "kuehne-nagel"
    assert kn.category == "Logisticien"
    assert "Kuehne + Nagel" in kn.aliases
    glp = actors.get_by_slug(conn, "glp")
    assert glp is not None and glp.case_sensitive is True
    # Deuxième synchronisation : rien ne change.
    assert actors.sync_from_yaml(conn, ACTORS_FILE) == actors.ActorSyncResult()


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "acteurs.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_removed_actor_becomes_untracked(conn: sqlite3.Connection, tmp_path: Path) -> None:
    actors.sync_from_yaml(conn, ACTORS_FILE)
    text = ACTORS_FILE.read_text(encoding="utf-8")
    reduced = text.replace("  - name: Heppner\n    category: Logisticien\n    aliases: []\n", "")
    reduced = reduced.replace(
        "  - name: Savills\n    category: Conseil\n", "  - name: Savills\n    category: Courtier\n"
    )
    assert reduced != text

    result = actors.sync_from_yaml(conn, _write(tmp_path, reduced))

    assert result.untracked == ["Heppner"]
    assert result.updated == ["Savills"]
    heppner = actors.get_by_name(conn, "Heppner")
    assert heppner is not None and heppner.tracked is False  # jamais supprimé
    total = len(load_actor_catalog(ACTORS_FILE))
    assert len(actors.list_all(conn)) == total
    assert len(actors.list_all(conn, tracked_only=True)) == total - 1
    savills = actors.get_by_name(conn, "Savills")
    assert savills is not None and savills.category == "Courtier"

    # Remise dans le YAML : de nouveau suivi.
    result = actors.sync_from_yaml(conn, ACTORS_FILE)
    assert "Heppner" in result.updated
    assert actors.get_by_name(conn, "Heppner").tracked is True  # type: ignore[union-attr]


def test_duplicate_name_is_rejected(conn: sqlite3.Connection, tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "actors:\n"
        "  - name: Prologis\n"
        "    category: Investisseur\n"
        "  - name: prologis\n"
        "    category: Autre\n",
    )
    with pytest.raises(ActorCatalogError) as excinfo:
        actors.sync_from_yaml(conn, path)
    message = str(excinfo.value)
    assert "ligne 4" in message and "prologis" in message and "doublon" in message
    assert actors.list_all(conn) == []  # base non modifiée


def test_duplicate_alias_between_actors_is_rejected() -> None:
    text = (
        "actors:\n"
        "  - name: DHL Supply Chain\n"
        "    aliases: [DHL]\n"
        "  - name: DHL Express\n"
        '    aliases: ["dhl"]\n'
    )
    with pytest.raises(ActorCatalogError, match=r"ligne 4 \(« DHL Express »\).*l'alias « dhl »"):
        parse_actor_catalog(text)


def test_variants_of_same_actor_are_allowed() -> None:
    entries = parse_actor_catalog(
        'actors:\n  - name: Kuehne+Nagel\n    aliases: ["Kuehne + Nagel", "KUEHNE+NAGEL"]\n'
    )
    assert entries[0].aliases == ("Kuehne + Nagel", "KUEHNE+NAGEL")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("actors:\n  - category: Conseil\n", "ligne 2 (acteur n°1) : le nom"),
        ('actors:\n  - name: "  "\n', "le nom de l'acteur est obligatoire"),
        ("actors:\n  - name: A\n    secteur: X\n", "champ(s) inconnu(s) secteur"),
        ("actors:\n  - name: A\n    aliases: B\n", "« aliases » doit être une liste"),
        ("actors:\n  - name: A\n    case_sensitive: oui\n", "« case_sensitive » doit valoir"),
        ("acteurs: []\n", "liste « actors: » non vide"),
        ("actors:\n  - name: [A\n", "YAML illisible"),
    ],
)
def test_invalid_catalog_messages(text: str, expected: str) -> None:
    with pytest.raises(ActorCatalogError) as excinfo:
        parse_actor_catalog(text)
    assert expected in str(excinfo.value)
    assert isinstance(excinfo.value, ConfigError)


def test_yaml_error_names_the_line() -> None:
    with pytest.raises(ActorCatalogError, match="ligne 3"):
        parse_actor_catalog("actors:\n  - name: A\n   aliases: [\n")


def test_slugify() -> None:
    assert slugify("Kuehne+Nagel") == "kuehne-nagel"
    assert slugify("E.Leclerc") == "e-leclerc"
    assert slugify("Cushman & Wakefield") == "cushman-wakefield"
