"""Tests de la version PDF à imprimer (fabriquée par le navigateur du poste)."""

from __future__ import annotations

import sqlite3
import stat
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from news_property.config import ConfigError, Settings, load_settings
from news_property.models import Article, Newsletter, Visit, VisitPhoto
from news_property.newsletter import pdf
from news_property.newsletter.theme import Theme, load_theme
from news_property.newsletter.view import NewsletterView, build_newsletter_view
from news_property.repositories import articles, visits

FAKE_BROWSER = """#!{python}
import sys
target = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--print-to-pdf="))
page = sys.argv[-1]
assert page.startswith("file://"), page
with open(target, "wb") as handle:
    handle.write(b"%PDF-1.7 faux " + page.encode())
"""


@pytest.fixture
def fake_browser(tmp_path: Path) -> Path:
    path = tmp_path / "faux-navigateur"
    path.write_text(FAKE_BROWSER.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def filled_view(
    app: Flask, settings: Settings, conn: sqlite3.Connection, newsletter: Newsletter
) -> tuple[NewsletterView, Theme]:
    nid = newsletter.id
    assert nid is not None
    for n in (1, 2):
        articles.add(conn, Article(nid, 1, f"Titre {n}", "Source", f"https://example.com/a{n}"))
    folder = settings.photos_dir / "2026-10"
    folder.mkdir(parents=True)
    Image.new("RGB", (800, 600), (141, 37, 98)).save(folder / "a.jpg")
    visit = visits.add(conn, Visit(nid, "Plateforme test", "Lyon", visit_date=date(2026, 10, 1)))
    assert visit.id is not None
    visits.add_photo(conn, VisitPhoto(visit.id, "2026-10/a.jpg"))
    theme = load_theme()
    return build_newsletter_view(conn, "2026-10", settings, theme), theme


def test_print_page_is_standalone(
    app: Flask, settings: Settings, filled_view: tuple[NewsletterView, Theme]
) -> None:
    view, theme = filled_view
    with app.app_context():
        html = pdf.print_html(view, theme, settings)
    assert "<style>" in html and ".nl-section-title" in html  # styles intégrés
    assert "/static/" not in html and "http://127.0.0.1" not in html
    assert html.count('class="qr"') == 2  # un QR code par article
    assert theme.logo_reverse.resolve().as_uri() in html
    assert (settings.photos_dir / view.photo_files[0]).as_uri() in html
    assert 'class="preview-bar"' not in html


@pytest.mark.skipif(sys.platform == "win32", reason="faux navigateur : script exécutable POSIX")
def test_make_pdf_with_a_browser(
    app: Flask, settings: Settings, filled_view: tuple[NewsletterView, Theme], fake_browser: Path
) -> None:
    view, theme = filled_view
    with app.app_context():
        data = pdf.newsletter_pdf(view, theme, replace(settings, browser=fake_browser))
    assert data.startswith(b"%PDF") and b"newsletter.html" in data
    # Fichiers de travail effacés.
    assert not list(settings.exports_dir.glob("pdf-*"))


@pytest.mark.skipif(sys.platform == "win32", reason="faux navigateur : script exécutable POSIX")
def test_browser_without_pdf_is_an_error(tmp_path: Path, settings: Settings) -> None:
    silent = tmp_path / "muet"
    silent.write_text(f"#!{sys.executable}\n", encoding="utf-8")
    silent.chmod(silent.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(pdf.PdfError, match="n'a pas produit de PDF"):
        pdf.make_pdf("<p>test</p>", silent, settings.exports_dir)


def test_no_browser_found(
    app: Flask,
    settings: Settings,
    filled_view: tuple[NewsletterView, Theme],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view, theme = filled_view
    monkeypatch.setattr(pdf, "browser_candidates", lambda env=None: [])
    with app.app_context(), pytest.raises(pdf.PdfError, match="CP_BROWSER"):
        pdf.newsletter_pdf(view, theme, settings)


def test_browser_search(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf.shutil, "which", lambda name: None)
    edge = tmp_path / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    candidates = pdf.browser_candidates({"ProgramFiles(x86)": str(tmp_path / "x86")})
    assert candidates[0] == edge
    assert pdf.find_browser(settings, {"ProgramFiles(x86)": str(tmp_path / "x86")}) is None
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    assert pdf.find_browser(settings, {"ProgramFiles(x86)": str(tmp_path / "x86")}) == edge
    chosen = tmp_path / "chrome.exe"
    assert pdf.find_browser(replace(settings, browser=chosen)) == chosen


def test_browser_command_uses_its_own_profile(tmp_path: Path) -> None:
    command = pdf.browser_command(
        Path("msedge.exe"), tmp_path / "page.html", tmp_path / "out.pdf", tmp_path / "profil"
    )
    assert "--headless=new" in command
    assert f"--print-to-pdf={tmp_path / 'out.pdf'}" in command
    assert f"--user-data-dir={tmp_path / 'profil'}" in command
    assert command[-1] == (tmp_path / "page.html").as_uri()


def test_browser_setting(tmp_path: Path) -> None:
    env = {"CP_DATA_DIR": str(tmp_path / "data"), "CP_OPEN_BROWSER": "false"}
    assert load_settings(env).browser is None
    with pytest.raises(ConfigError, match="CP_BROWSER"):
        load_settings({**env, "CP_BROWSER": str(tmp_path / "absent.exe")})
    exe = tmp_path / "msedge.exe"
    exe.write_bytes(b"")
    assert load_settings({**env, "CP_BROWSER": f'"{exe}"'}).browser == exe
