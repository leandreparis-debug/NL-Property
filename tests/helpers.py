"""Outils partagés par les tests (application web, jeton CSRF, thème de test)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from werkzeug.test import TestResponse

from news_property.newsletter.theme import THEME_FILE

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Le client de test s'adresse à l'application comme le navigateur : Host 127.0.0.1:<port>.
TEST_HOST = "127.0.0.1:5000"
_CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def web_app(settings: Any, **kwargs: Any) -> Flask:
    """Application de test joignable comme en vrai (Host 127.0.0.1:5000, CSRF actif)."""
    from news_property import create_app

    app = create_app(settings, **kwargs)
    app.config["SERVER_NAME"] = TEST_HOST
    return app


def csrf_token(client: FlaskClient) -> str:
    """Jeton CSRF de la session du client (lu dans la page d'accueil)."""
    match = _CSRF_META.search(client.get("/").get_data(as_text=True))
    assert match, "jeton CSRF absent de la page"
    return match.group(1)


def post_form(
    client: FlaskClient, url: str, data: dict[str, Any] | None = None, **kwargs: Any
) -> TestResponse:
    """Envoie un formulaire comme le navigateur, avec le jeton CSRF de la session."""
    payload = dict(data or {})
    payload.setdefault("csrf_token", csrf_token(client))
    return client.post(url, data=payload, **kwargs)


def html_of(response: TestResponse) -> str:
    """Corps de la réponse en texte."""
    return response.get_data(as_text=True)


def write_theme(tmp_path: Path, **overrides: str) -> Path:
    """Copie du thème par défaut dans tmp_path, logo compris, avec des valeurs remplacées
    (clé « colors.text », « logo »...)."""
    logo = tmp_path / "logo.png"
    Image.new("RGB", (60, 20), (141, 37, 98)).save(logo)
    text = THEME_FILE.read_text(encoding="utf-8")
    logo_value = overrides.pop("logo", logo.as_posix())
    text = text.replace("logo: config/assets/logo-carrefour-property.png", f"logo: {logo_value}")
    text = text.replace(
        "logo_reverse: config/assets/logo-carrefour-property-blanc.png",
        f"logo_reverse: {logo.as_posix()}",
    )
    for key, value in overrides.items():
        section, _, name = key.rpartition(".")
        assert section in ("colors", "fonts", "section_colors")
        pattern = rf"(\n{section}:\n(?:  .*\n|\s*#.*\n)*?  {name}: )\"[^\"]*\""
        text, count = re.subn(pattern, rf'\1"{value}"', text, count=1)
        assert count == 1, key
    path = tmp_path / "theme.yaml"
    path.write_text(text, encoding="utf-8")
    return path
