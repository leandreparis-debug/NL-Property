"""Tests du mois de démonstration (janvier 2000) : contenu fictif, liens example.com, exports."""

from __future__ import annotations

import re
import sqlite3

import pytest
from flask import Flask
from flask.testing import FlaskClient

from news_property.config import Settings
from news_property.newsletter.email import GMAIL_CLIP_MARGIN, build_email
from news_property.newsletter.theme import load_theme
from news_property.newsletter.view import build_newsletter_view
from news_property.repositories import articles, newsletters, visits
from news_property.tools import demo
from tests.helpers import html_of


def test_demo_creates_january_2000_only(conn: sqlite3.Connection, settings: Settings) -> None:
    messages: list[str] = []
    assert demo.main([], settings=settings, out=messages.append) == 0
    assert messages[0].startswith(f"Démonstration créée : {len(demo.DEMO_ARTICLES)} articles")
    assert [n.month for n in newsletters.list_all(conn)] == ["2000-01"]
    newsletter = newsletters.get(conn, "2000-01")
    assert newsletter is not None and newsletter.id is not None
    items = articles.list_for_newsletter(conn, newsletter.id)
    assert len(items) == len(demo.DEMO_ARTICLES)
    # Tout est marqué, aucun lien inventé : example.com uniquement.
    assert all(a.title.startswith("[DÉMO] ") for a in items)
    assert all(a.url.startswith("https://example.com/demo/2000-01/") for a in items)
    assert all(a.source_name == demo.DEMO_SOURCE for a in items)
    # La détection a tourné : transactions et surfaces repérées sur les articles fictifs.
    assert any(a.is_transaction and a.transaction_source == "auto" for a in items)
    assert {a.surface_sqm for a in items} >= {60000, 40000, 25000}
    # Acteurs du référentiel reliés aux articles de la rubrique 2.
    assert all(a.actor_id is not None for a in items if a.rubric == 2)
    # Contenu interne : visites avec photos réelles sur le disque.
    visit_items = visits.list_for_newsletter(conn, newsletter.id)
    assert len(visit_items) == 2 and all(v.site_name.startswith("[DÉMO] ") for v in visit_items)
    photos = [p for v in visit_items for p in visits.list_photos(conn, v.id or 0)]
    assert len(photos) == 3
    for photo in photos:
        assert (settings.photos_dir / photo.file_name).is_file()
        assert photo.file_name.startswith("2000-01/")


def test_demo_refuses_to_overwrite_unless_reset(
    conn: sqlite3.Connection, settings: Settings
) -> None:
    assert demo.main([], settings=settings, out=lambda _: None) == 0
    assert demo.main([], settings=settings, out=lambda _: None) == 2
    newsletter = newsletters.get(conn, "2000-01")
    assert newsletter is not None and newsletter.id is not None
    first_photos = {
        p.file_name
        for v in visits.list_for_newsletter(conn, newsletter.id)
        for p in visits.list_photos(conn, v.id or 0)
    }
    assert demo.main(["--reset"], settings=settings, out=lambda _: None) == 0
    # Recréée à l'identique : anciennes photos effacées, nouvelles écrites.
    for name in first_photos:
        assert not (settings.photos_dir / name).exists()
    assert len(list((settings.photos_dir / "2000-01").glob("*.jpg"))) == 3


def test_demo_fills_every_section(app: Flask, conn: sqlite3.Connection, settings: Settings) -> None:
    assert demo.main([], settings=settings, out=lambda _: None) == 0
    view = build_newsletter_view(conn, "2000-01", settings, load_theme())
    assert [s.display_number for s in view.sections] == [1, 2, 3, 4, 5, 6]
    assert [f.value for f in view.front] == [2, 3, len(demo.DEMO_ARTICLES)]
    with app.app_context():
        export = build_email(conn, "2000-01", settings)
    assert export.html_size < GMAIL_CLIP_MARGIN
    assert export.warnings == ()
    links = set(re.findall(r'href="([^"]+)"', export.html))
    assert links and all(link.startswith("https://example.com/demo/") for link in links)


def test_demo_pages(client: FlaskClient, settings: Settings) -> None:
    assert demo.main([], settings=settings, out=lambda _: None) == 0
    html = html_of(client.get("/newsletters/2000-01/rubrique-1"))
    assert "[DÉMO]" in html and "&lt;script&gt;alert(" in html and "<script>alert" not in html
    preview = html_of(client.get("/newsletters/2000-01/apercu"))
    assert "Janvier 2000" in preview
    assert preview.count('class="qr"') == len(demo.DEMO_ARTICLES)
    assert preview.count('<figure class="nl-photo">') == 2
    assert "/photos/2000-01/" in preview
    assert "nl-event-wide" in preview and "Du 18 au 20 janvier 2000" in preview
    assert "Du 3 au 7 janvier 2000" in preview and "Le 14 janvier 2000" in preview


@pytest.mark.parametrize("label", ["DÉMO", "DEMO"])
def test_demo_photo_is_a_marked_jpeg(label: str) -> None:
    from io import BytesIO

    from PIL import Image

    data = demo.demo_photo((141, 37, 98), label, 1)
    with Image.open(BytesIO(data)) as image:
        assert image.format == "JPEG" and image.size == (2400, 1800)
