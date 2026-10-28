"""Tests des pages de collecte et de la commande en ligne."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from flask import Flask
from flask.testing import FlaskClient

from news_property.collect.runner import Progress
from news_property.collect.views import CollectionJob, _registry
from news_property.config import Settings
from news_property.db.connection import get_connection
from news_property.http_client import Fetcher
from news_property.repositories import collection_runs, newsletters
from news_property.tools import collect as collect_tool
from tests.helpers import post_form, web_app
from tests.test_collect_runner import FEED_A, NOW, _site_routes, write_sources


@pytest.fixture
def collect_app(settings: Settings, tmp_path: Path) -> Flask:
    app = web_app(settings)
    app.config.update(
        CP_CLOCK=lambda: NOW,
        CP_FETCHER_FACTORY=lambda: Fetcher(sleep=lambda _: None),
        CP_SOURCES_FILE=write_sources(tmp_path, [("Site A", FEED_A)]),
    )
    return app


@pytest.fixture
def web(collect_app: Flask) -> FlaskClient:
    return collect_app.test_client()


def test_home_enables_collect_button(web: FlaskClient) -> None:
    html = web.get("/").get_data(as_text=True)
    assert 'href="/collecte/"' in html and "Collecter la veille" in html


def test_form_defaults_to_previous_month(web: FlaskClient) -> None:
    html = web.get("/collecte/").get_data(as_text=True)
    assert 'value="2026-10"' in html
    assert "Lancer la collecte" in html


def test_missing_sources_file_is_explained(collect_app: Flask, tmp_path: Path) -> None:
    collect_app.config["CP_SOURCES_FILE"] = tmp_path / "absent.yaml"
    html = collect_app.test_client().get("/collecte/").get_data(as_text=True)
    assert "sources.proposed.yaml" in html
    assert "disabled" in html
    response = post_form(collect_app.test_client(), "/collecte/", {"mois": "2026-10"})
    assert response.status_code == 400


def test_collection_in_background_then_summary(
    collect_app: Flask, web: FlaskClient, mock_http: respx.MockRouter
) -> None:
    _site_routes(mock_http)

    response = post_form(web, "/collecte/", {"mois": "2026-10"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/collecte/suivi")
    job = _registry(collect_app).job
    assert job is not None and job.thread is not None
    job.thread.join(timeout=30)
    assert job.state == "done", job.error

    html = web.get("/collecte/suivi").get_data(as_text=True)
    assert "Terminée" in html
    assert 'http-equiv="refresh"' not in html
    assert "Articles ajoutés, rubrique 1" in html and "Site A" in html
    # Le bilan reste consultable depuis la page du formulaire (lu en base).
    html = web.get("/collecte/?mois=2026-10").get_data(as_text=True)
    assert "Dernière collecte de Octobre 2026" in html


def test_status_page_refreshes_while_running(collect_app: Flask, web: FlaskClient) -> None:
    job = CollectionJob(month="2026-10", progress=Progress("Site A", 1, 3, 12, 4))
    _registry(collect_app).job = job
    html = web.get("/collecte/suivi").get_data(as_text=True)
    assert 'http-equiv="refresh"' in html
    assert "Source 1 sur 3" in html and "Site A" in html and "articles ajoutés : 4" in html


def test_second_collection_is_refused(collect_app: Flask, web: FlaskClient) -> None:
    _registry(collect_app).job = CollectionJob(month="2026-10")  # en cours
    response = post_form(web, "/collecte/", {"mois": "2026-09"})
    assert response.status_code == 409
    assert "déjà en cours" in response.get_data(as_text=True)


def test_collection_running_in_another_process_is_refused(
    collect_app: Flask, web: FlaskClient, settings: Settings
) -> None:
    conn = get_connection(settings)
    try:
        newsletter = newsletters.get_or_create(conn, "2026-10")
        collection_runs.start(conn, newsletter.id, NOW)  # type: ignore[arg-type]
    finally:
        conn.close()
    response = post_form(web, "/collecte/", {"mois": "2026-10"})
    assert response.status_code == 409
    assert "déjà en cours" in response.get_data(as_text=True)
    assert _registry(collect_app).job is None


def test_invalid_month_is_rejected(web: FlaskClient) -> None:
    response = post_form(web, "/collecte/", {"mois": "octobre"})
    assert response.status_code == 400
    assert "AAAA-MM" in response.get_data(as_text=True)


def test_cross_site_post_is_forbidden(collect_app: Flask, web: FlaskClient) -> None:
    response = post_form(
        web, "/collecte/", {"mois": "2026-10"}, headers={"Origin": "https://malveillant.example"}
    )
    assert response.status_code == 403
    assert "Action refusée" in response.get_data(as_text=True)
    assert _registry(collect_app).job is None
    ok = post_form(
        web, "/collecte/", {"mois": "pas-un-mois"}, headers={"Origin": "http://127.0.0.1:5000"}
    )
    assert ok.status_code == 400  # même origine : la requête est traitée


# --------------------------------------------------------------------------- ligne de commande


def test_cli_collect(
    settings: Settings, fetcher: Fetcher, mock_http: respx.MockRouter, tmp_path: Path
) -> None:
    _site_routes(mock_http)
    output: list[str] = []
    code = collect_tool.main(
        [],
        settings=settings,
        clock=lambda: NOW,
        fetcher=fetcher,
        sources_path=write_sources(tmp_path, [("Site A", FEED_A)]),
        out=output.append,
    )
    text = "\n".join(output)
    assert code == 0
    assert "Collecte de la veille 2026-10" in text  # mois précédent par défaut
    assert "rubrique 1 (immobilier logistique) : 2" in text
    assert "Lien : https://site-a.example/articles/prologis-100e-entrepot" in text
    assert "Premières lignes : Le développeur Prologis a livré" in text


def test_cli_config_error(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = collect_tool.main(
        ["--month", "2026-10"],
        settings=settings,
        clock=lambda: datetime(2026, 11, 3, tzinfo=UTC),
        sources_path=tmp_path / "absent.yaml",
        out=lambda _: None,
    )
    assert code == 2
    assert "sources.proposed.yaml" in capsys.readouterr().err
