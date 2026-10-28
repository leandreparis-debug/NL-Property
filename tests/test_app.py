"""Tests de l'application web : accueil, pages d'erreur, menu."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from news_property import __version__
from news_property.config import Settings
from news_property.dates import format_month_fr
from news_property.ui import NAV, active_section
from tests.helpers import html_of, web_app


def test_index(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = html_of(response)
    assert "News Property" in html
    assert format_month_fr(date.today()) in html
    for label in ("Collecter la veille", "Relire", "Contenu interne", "Exporter", "Thème"):
        assert label in html


def test_index_links_only_the_functions_delivered(client: FlaskClient) -> None:
    html = html_of(client.get("/"))
    assert 'href="/theme"' in html
    # Les fonctions des étapes suivantes sont annoncées sans lien.
    assert "À venir dans une prochaine étape" in html


def test_health(client: FlaskClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "version": __version__}


def test_404_page_in_french(client: FlaskClient) -> None:
    response = client.get("/cette-page-n-existe-pas")
    assert response.status_code == 404
    html = html_of(response)
    assert "Page introuvable" in html
    assert "La page demandée" in html


def test_500_page_hides_traceback(settings: Settings) -> None:
    app: Flask = web_app(settings)

    @app.get("/boom")
    def boom() -> str:
        raise RuntimeError("détail secret de l'erreur")

    response = app.test_client().get("/boom")
    assert response.status_code == 500
    html = html_of(response)
    assert "Erreur interne" in html
    assert "Traceback" not in html
    assert "RuntimeError" not in html
    assert "détail secret" not in html


def test_settings_stored_in_app(app: Flask, settings: Settings) -> None:
    assert app.config["CP_SETTINGS"] is settings


def test_format_month_fr() -> None:
    assert format_month_fr(date(2026, 10, 9)) == "Octobre 2026"
    assert format_month_fr(date(2026, 8, 1)) == "Août 2026"


def test_sidebar_menu_marks_the_current_section(client: FlaskClient) -> None:
    html = html_of(client.get("/"))
    assert "Accueil" in html and "Thème" in html
    assert 'src="/theme/logo/blanc"' in html  # logo en réserve blanche
    assert re.search(r'class="side-link is-active" href="/" aria-current="page"', html)
    theme = html_of(client.get("/theme"))
    assert re.search(r'is-active" href="/theme" aria-current="page"', theme)


def test_menu_hides_the_functions_not_delivered_yet(client: FlaskClient) -> None:
    html = html_of(client.get("/"))
    delivered = [label for _, label, endpoint in NAV if endpoint in ("index", "newsletter.theme")]
    assert delivered == ["Accueil", "Thème"]
    assert 'href="/collecte/"' not in html


@pytest.mark.parametrize(
    ("endpoint", "section"),
    [
        ("index", "accueil"),
        (None, "accueil"),
        ("review.month", "relecture"),
        ("content.openings", "relecture"),
        ("content.start", "contenu"),
        ("collect.status", "collecte"),
        ("newsletter.export_page", "export"),
        ("newsletter.exports", "export"),
        ("newsletter.theme", "theme"),
        ("newsletter.preview", "relecture"),
    ],
)
def test_active_section(endpoint: str | None, section: str) -> None:
    assert active_section(endpoint) == section


def test_menu_icons_stay_visible_on_the_sidebar() -> None:
    from news_property.newsletter.theme import contrast_ratio
    from news_property.ui import _visible_on

    colors = _visible_on(("#8D2562", "#5E1741", "#E0B3CC"), "#5E1741")
    assert "#5E1741" not in colors
    assert all(contrast_ratio(color, "#5E1741") >= 2 for color in colors)


def test_app_creates_database_with_actors(app: Flask, settings: Settings) -> None:
    from contextlib import closing

    from news_property.db.connection import current_version, db_path, get_connection
    from news_property.db.connection import list_migrations as migrations
    from news_property.repositories import actors

    assert db_path(settings).is_file()
    with closing(get_connection(settings)) as conn:
        assert current_version(conn) == migrations()[-1].version
        assert len(actors.list_all(conn, tracked_only=True)) >= 30


def test_invalid_actors_file_prevents_startup(settings: Settings, tmp_path: Path) -> None:
    from news_property import create_app
    from news_property.actor_catalog import ActorCatalogError

    bad = tmp_path / "acteurs.yaml"
    bad.write_text("actors:\n  - name: A\n  - name: a\n", encoding="utf-8")
    with pytest.raises(ActorCatalogError, match="ligne 3"):
        create_app(settings, actors_file=bad)


def test_one_connection_per_request_closed_at_teardown(app: Flask) -> None:
    from flask import g

    from news_property.db.connection import get_db

    with app.app_context():
        conn = get_db()
        assert get_db() is conn
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert "db" in g
    with pytest.raises(Exception, match="closed"):
        conn.execute("SELECT 1")
