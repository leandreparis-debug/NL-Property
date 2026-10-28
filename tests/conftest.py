"""Fixtures communes : toutes les données de test restent dans tmp_path."""

from __future__ import annotations

import logging
import socket
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from flask import Flask
from flask.testing import FlaskClient

from news_property.config import Settings, load_settings
from news_property.db.connection import get_connection, init_db
from news_property.http_client import Fetcher
from news_property.models import Newsletter
from news_property.repositories import newsletters
from tests.helpers import web_app


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interdit toute connexion réseau réelle pendant les tests."""

    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Accès réseau interdit pendant les tests.")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Retire, après chaque test, les gestionnaires de log installés par l'application."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_news_property_handler", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Dossier de données isolé dans tmp_path."""
    return tmp_path / "data"


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    """Paramètres de test pointant vers le dossier temporaire."""
    return load_settings({"CP_DATA_DIR": str(data_dir), "CP_OPEN_BROWSER": "false"})


@pytest.fixture
def app(settings: Settings) -> Flask:
    """Application Flask de test."""
    return web_app(settings)


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Client HTTP de test."""
    return app.test_client()


@pytest.fixture
def conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    """Connexion à une base migrée, dans le dossier temporaire."""
    init_db(settings)
    connection = get_connection(settings)
    yield connection
    connection.close()


@pytest.fixture
def newsletter(conn: sqlite3.Connection) -> Newsletter:
    """Newsletter d'octobre 2026."""
    return newsletters.get_or_create(conn, "2026-10")


@pytest.fixture
def fetcher() -> Iterator[Fetcher]:
    """Récupérateur HTTP sans pause réelle (sleep neutralisé)."""
    sleeps: list[float] = []
    with Fetcher(sleep=sleeps.append) as instance:
        instance.sleeps = sleeps  # type: ignore[attr-defined]
        yield instance


@pytest.fixture
def mock_http() -> Iterator[respx.MockRouter]:
    """Simulation des appels httpx (déclarer les routes avec tests.helpers.serve)."""
    with respx.mock(assert_all_called=False) as router:
        yield router
