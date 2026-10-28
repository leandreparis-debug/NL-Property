"""Tests de sécurité : CSRF, contrôle du Host et de l'origine, clé secrète, cookie."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from news_property.config import Settings
from news_property.security import load_or_create_secret_key, secret_key_path
from tests.helpers import csrf_token, html_of, web_app


def _with_post(settings: Settings) -> Flask:
    """Application de test avec une route POST protégée, pour vérifier CSRF et origine."""
    app = web_app(settings)

    @app.post("/essai")
    def essai() -> str:
        return "accepté"

    return app


def test_post_without_csrf_token_is_rejected(settings: Settings) -> None:
    client = _with_post(settings).test_client()
    response = client.post("/essai", data={"valeur": "1"})
    assert response.status_code == 400
    assert "Formulaire expiré" in html_of(response)
    assert client.post("/essai", data={"csrf_token": "faux"}).status_code == 400
    ok = client.post("/essai", data={"csrf_token": csrf_token(client)})
    assert ok.status_code == 200 and ok.get_data(as_text=True) == "accepté"


def test_foreign_origin_is_rejected(settings: Settings) -> None:
    client = _with_post(settings).test_client()
    token = csrf_token(client)
    foreign = client.post(
        "/essai", data={"csrf_token": token}, headers={"Origin": "http://evil.example"}
    )
    assert foreign.status_code == 403
    assert "Action refusée" in html_of(foreign)
    referer = client.post(
        "/essai", data={"csrf_token": token}, headers={"Referer": "http://evil.example/page"}
    )
    assert referer.status_code == 403
    same = client.post(
        "/essai", data={"csrf_token": token}, headers={"Origin": "http://127.0.0.1:5000"}
    )
    assert same.status_code == 200


def test_foreign_host_is_rejected(client: FlaskClient) -> None:
    for host in ("evil.example", "evil.example:5000", "127.0.0.1:6000", "127.0.0.1"):
        response = client.get("/", headers={"Host": host})
        assert response.status_code == 400, host
        assert "Adresse non autorisée" in html_of(response)
    assert client.get("/", headers={"Host": "localhost:5000"}).status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1:5000"}).status_code == 200


def test_secret_key_is_created_once(settings: Settings, app: Flask) -> None:
    key_file = secret_key_path(settings)
    assert key_file.is_file()
    key = key_file.read_text(encoding="ascii")
    assert len(key) == 64 and app.config["SECRET_KEY"] == key
    assert web_app(settings).config["SECRET_KEY"] == key
    assert load_or_create_secret_key(settings) == key


def test_session_cookie_is_strict(app: Flask, client: FlaskClient) -> None:
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
    response = client.get("/")  # le jeton CSRF de la page ouvre la session : cookie posé
    cookies = response.headers.getlist("Set-Cookie")
    assert cookies and all("SameSite=Strict" in cookie for cookie in cookies)
    assert app.config["WTF_CSRF_TIME_LIMIT"] is None
    assert app.config["MAX_CONTENT_LENGTH"] == 15 * 1024 * 1024


def test_no_template_uses_safe_filter() -> None:
    """Aucun gabarit n'utilise |safe : les textes externes sont toujours échappés."""
    from pathlib import Path

    import news_property

    templates = Path(news_property.__file__).parent / "templates"
    for path in templates.rglob("*.html"):
        assert "|safe" not in path.read_text(encoding="utf-8"), path
        assert "| safe" not in path.read_text(encoding="utf-8"), path
