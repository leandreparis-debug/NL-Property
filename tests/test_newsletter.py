"""Tests de l'étape 8 : dates en français, vue de la newsletter, QR codes, aperçu."""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

from news_property.config import Settings
from news_property.models import (
    Absence,
    Article,
    DocumentLink,
    Event,
    Newsletter,
    Visit,
    VisitPhoto,
)
from news_property.newsletter import qr
from news_property.newsletter.dates import format_day, format_month, format_range
from news_property.newsletter.view import (
    EmptyNewsletterError,
    EventItem,
    NewsletterNotFoundError,
    build_newsletter_view,
    event_rows,
)
from news_property.repositories import (
    absences,
    articles,
    document_links,
    events,
    newsletters,
    visits,
)
from tests.helpers import html_of, write_theme

SVG_NS = "{http://www.w3.org/2000/svg}"
PREVIEW = "/newsletters/2026-10/apercu"


# --------------------------------------------------------------------------- dates


def test_first_day_of_month_is_written_1er() -> None:
    assert format_day(date(2026, 1, 1)) == "1er janvier 2026"
    assert format_day(date(2026, 10, 12)) == "12 octobre 2026"


def test_months_with_accents_and_capital() -> None:
    assert format_month("2026-10") == "Octobre 2026"
    assert format_day(date(2026, 2, 3)) == "3 février 2026"
    assert format_month("2026-08") == "Août 2026"
    assert format_month("2026-12") == "Décembre 2026"


def test_datetime_is_formatted_in_paris_time() -> None:
    # 31 décembre 23 h 30 UTC = 1er janvier 0 h 30 à Paris.
    assert format_day(datetime(2025, 12, 31, 23, 30, tzinfo=UTC)) == "1er janvier 2026"


def test_ranges_in_french() -> None:
    assert format_range(date(2026, 11, 12), None) == "le 12 novembre 2026"
    assert format_range(date(2026, 11, 12), date(2026, 11, 12)) == "le 12 novembre 2026"
    assert format_range(date(2026, 11, 17), date(2026, 11, 19)) == "du 17 au 19 novembre 2026"
    assert (
        format_range(date(2026, 11, 30), date(2026, 12, 2)) == "du 30 novembre au 2 décembre 2026"
    )
    assert (
        format_range(date(2026, 12, 28), date(2027, 1, 3))
        == "du 28 décembre 2026 au 3 janvier 2027"
    )
    assert format_range(date(2026, 11, 1), date(2026, 11, 2)) == "du 1er au 2 novembre 2026"


# --------------------------------------------------------------------------- vue


def _article(newsletter: Newsletter, rubric: int, n: int, **kwargs: object) -> Article:
    values: dict[str, object] = {
        "title": f"Titre {n}",
        "source_name": "Source test",
        "url": f"https://example.com/a{n}",
        "published_at": datetime(2026, 10, n, 8, tzinfo=UTC),
    }
    values.update(kwargs)
    return Article(newsletter_id=newsletter.id, rubric=rubric, **values)  # type: ignore[arg-type]


def _fill(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    """Contenu varié : articles retenus ou non, une transaction, une visite avec deux
    photos, deux rendez-vous (dont un avec résumé), une absence, deux documents."""
    nid = newsletter.id
    assert nid is not None
    for n, selected in ((1, True), (2, False), (3, True)):
        articles.add(conn, _article(newsletter, 1, n, selected=selected))
    articles.add(
        conn,
        _article(
            newsletter,
            2,
            4,
            title="<script>alert(1)</script>",
            is_transaction=True,
            surface_sqm=40000,
        ),
    )
    visit = visits.add(
        conn,
        Visit(nid, "Plateforme test", "Lyon (69)", visit_date=date(2026, 10, 1), surface_sqm=42000),
    )
    assert visit.id is not None
    for name, caption in (("2026-10/a.jpg", "Façade"), ("2026-10/b.jpg", None)):
        visits.add_photo(conn, VisitPhoto(visit.id, name, caption=caption))
    events.add(conn, Event(nid, "Comité", event_date=date(2026, 11, 5), location="Massy"))
    events.add(
        conn,
        Event(
            nid,
            "Salon",
            event_date=date(2026, 11, 17),
            end_date=date(2026, 11, 19),
            summary="Première ligne.\nSeconde ligne du résumé.",
        ),
    )
    absences.add(
        conn, Absence(nid, "Chloé G.", date(2026, 11, 2), date(2026, 11, 6), reason="Congés")
    )
    document_links.replace_all(
        conn,
        nid,
        [
            DocumentLink(nid, 1, "https://example.com/doc1", label="Tableau de bord"),
            DocumentLink(nid, 2, "https://example.com/doc2"),
        ],
    )


def test_view_keeps_only_retained_articles_in_order(
    conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    _fill(conn, newsletter)
    view = build_newsletter_view(conn, "2026-10", settings)
    s1, s2, s3, s4, s5, s6, s7 = view.sections
    assert [item.title for item in s1.items] == ["Titre 1", "Titre 3"]
    assert s1.items[0].date_label == "1er octobre 2026"
    assert s2.items[0].transaction is True and s2.items[0].surface_label == "40 000 m²"
    assert s1.items[0].surface_label is None
    # Pas d'article de réglementation : seule la rubrique 3 est masquée.
    assert [s.hidden for s in view.sections] == [False, False, True, False, False, False, False]
    s3, s4, s5, s6 = s4, s5, s6, s7
    assert view.month_label == "Octobre 2026"
    # Visite : photo principale d'abord, surface et date en français.
    visit = s3.items[0]
    assert visit.main_photo.file_name == "2026-10/a.jpg"
    assert [p.file_name for p in visit.other_photos] == ["2026-10/b.jpg"]
    assert visit.date_label == "1er octobre 2026" and visit.surface_label == "42 000 m²"
    assert view.photo_files == ("2026-10/a.jpg", "2026-10/b.jpg")
    # Calendrier et absences : périodes en toutes lettres.
    assert [e.date_label for e in s4.items] == ["Le 5 novembre 2026", "Du 17 au 19 novembre 2026"]
    assert s4.items[1].wide and s4.items[1].summary_lines == (
        "Première ligne.",
        "Seconde ligne du résumé.",
    )
    assert s5.items[0].period_label == "Du 2 au 6 novembre 2026"
    assert [d.label for d in s6.items] == ["Tableau de bord", "Voir le document"]
    assert view.article_urls == (
        "https://example.com/a1",
        "https://example.com/a3",
        "https://example.com/a4",
    )


def test_view_hides_empty_sections_and_counts_toc(
    conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    nid = newsletter.id
    assert nid is not None
    articles.add(conn, _article(newsletter, 2, 1))
    events.add(conn, Event(nid, "Comité"))
    absences.add(conn, Absence(nid, "Marc D.", date(2026, 11, 12)))
    view = build_newsletter_view(conn, "2026-10", settings)
    assert [s.number for s in view.visible_sections] == [2, 5, 6]
    # Numéros affichés sans trou quand une rubrique vide est masquée.
    assert [s.display_number for s in view.visible_sections] == [1, 2, 3]
    assert [(e.number, e.title, e.count_label) for e in view.toc] == [
        (1, "Les acteurs du marché", "1 article"),
        (2, "Calendrier à venir", "1 rendez-vous"),
        (3, "Congés et absences de l'équipe", "1 absence"),
    ]
    assert view.sections[4].items[0].date_label is None
    assert view.sections[5].items[0].period_label == "Le 12 novembre 2026"


def test_front_page_matches_front_page_figures(
    conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    nid = newsletter.id
    assert nid is not None
    articles.add(conn, _article(newsletter, 2, 1))
    events.add(conn, Event(nid, "Comité"))
    view = build_newsletter_view(conn, "2026-10", settings)
    figures = newsletters.front_page_figures(conn, nid)
    assert [f.value for f in view.front] == [figures.visits, figures.events, figures.articles]
    assert [f.label for f in view.front] == [
        "visite d'entrepôt",
        "rendez-vous à venir",
        "actualité retenue",
    ]
    # Pas de lien vers la rubrique des visites (vide, donc absente) ; les actualités
    # renvoient vers la première rubrique d'articles publiée (ici la 2).
    assert [f.anchor for f in view.front] == [None, "rubrique-5", "rubrique-2"]


def test_empty_newsletter_raises_clear_error(
    conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    with pytest.raises(EmptyNewsletterError, match="Octobre 2026 est encore vide"):
        build_newsletter_view(conn, "2026-10", settings)
    with pytest.raises(NewsletterNotFoundError):
        build_newsletter_view(conn, "2026-11", settings)


def test_event_rows_put_summaries_on_their_own_row() -> None:
    short = [EventItem(f"Rendez-vous {n}", None, None, None) for n in range(3)]
    long = EventItem("Salon", "Du 17 au 19 novembre 2026", None, "Rendez-vous.\nSuite prévue.")
    assert long.wide and long.summary_lines == ("Rendez-vous.", "Suite prévue.")
    rows = event_rows((short[0], long, short[1], short[2]))
    assert [[item.title for item in row] for row in rows] == [
        ["Rendez-vous 0"],
        ["Salon"],
        ["Rendez-vous 1", "Rendez-vous 2"],
    ]


# --------------------------------------------------------------------------- QR codes


def _matrix_from_svg(svg: str) -> list[list[bool]]:
    """Reconstruit la matrice à partir du chemin SVG (un carré par module noir)."""
    root = ET.fromstring(svg)
    size = int(root.get("viewBox", "").split()[2])
    matrix = [[False] * size for _ in range(size)]
    path = root.find(f"{SVG_NS}path")
    assert path is not None
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", path.get("d", "")):
        matrix[int(y)][int(x)] = True
    return matrix


def test_qr_svg_is_valid_xml_and_matches_url(tmp_path: Path) -> None:
    url = "https://example.com/article?id=1&x=é"
    svg = qr.qr_svg(url)
    root = ET.fromstring(svg)
    assert root.tag == f"{SVG_NS}svg"
    assert _matrix_from_svg(svg) == qr.qr_matrix(url)
    assert _matrix_from_svg(svg) != qr.qr_matrix("https://example.com/autre")


def test_qr_cache_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/cache"
    first = qr.qr_svg(url, tmp_path)
    cached = tmp_path / f"{qr.url_digest(url)}.svg"
    assert cached.is_file()

    def fail(_: str) -> list[list[bool]]:
        raise AssertionError("le cache aurait dû être utilisé")

    monkeypatch.setattr(qr, "qr_matrix", fail)
    assert qr.qr_svg(url, tmp_path) == first


def test_qr_png_is_cached(tmp_path: Path) -> None:
    path = qr.qr_png("https://example.com/png", cache_dir=tmp_path)
    assert path.parent == tmp_path and path.suffix == ".png"
    mtime = path.stat().st_mtime_ns
    assert qr.qr_png("https://example.com/png", cache_dir=tmp_path) == path
    assert path.stat().st_mtime_ns == mtime
    with Image.open(path) as image:
        assert image.format == "PNG"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com/x", "", "/relatif"])
def test_qr_refuses_non_http_links(url: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        qr.qr_svg(url)
    with pytest.raises(ValueError):
        qr.qr_png(url, cache_dir=tmp_path)


# --------------------------------------------------------------------------- aperçu


@pytest.fixture
def filled_client(
    client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> FlaskClient:
    _fill(conn, newsletter)
    return client


def test_preview_of_filled_month(filled_client: FlaskClient, settings: Settings) -> None:
    response = filled_client.get(PREVIEW)
    assert response.status_code == 200
    html = html_of(response)
    for title in (
        "À la une ce mois-ci",
        "Actualités de l&#39;immobilier logistique",
        "Les acteurs du marché",
        "Nos visites d&#39;entrepôts",
        "Calendrier à venir",
        "Congés et absences de l&#39;équipe",
        "Documents utiles",
    ):
        assert title in html, title
    assert "Octobre 2026" in html
    # Charte : bandeau avec logo en réserve blanche et motif, logo couleur en pied de page,
    # rubriques numérotées sur deux chiffres.
    assert 'src="/theme/logo/blanc"' in html and 'src="/theme/logo"' in html
    assert 'class="nl-motif"' in html
    numbers = re.findall(r'class="nl-section-number">(\d+)<', html)
    assert numbers == ["01", "02", "03", "04", "05", "06"]
    # Un QR code par article retenu, juste sous son titre ; l'article non retenu est absent.
    titles = re.findall(r'nl-article-title"><a href="([^"]+)"', html)
    assert titles == ["https://example.com/a1", "https://example.com/a3", "https://example.com/a4"]
    assert html.count('class="qr"') == 3
    assert "Titre 2" not in html
    assert ">Transaction<" in html and "40 000 m²" in html
    # Photos principales servies par l'application, la seconde en vignette.
    assert html.count('<figure class="nl-photo">') == 1
    assert html.count('<div class="nl-thumbs">') == 1
    assert 'src="/photos/2026-10/a.jpg"' in html
    # Calendrier, absences, documents (adresse écrite en clair pour l'impression).
    assert "nl-event-wide" in html and "Première ligne.<br>Seconde ligne" in html
    assert "Du 2 au 6 novembre 2026" in html and "Congés" in html
    assert 'class="nl-url">https://example.com/doc2<' in html and "Voir le document" in html
    assert 'rel="noopener noreferrer"' in html
    # QR codes mis en cache dans <données>/exports/qr.
    assert len(list((settings.exports_dir / "qr").glob("*.svg"))) == 3


def test_preview_qr_follows_its_article(filled_client: FlaskClient) -> None:
    html = html_of(filled_client.get(PREVIEW))
    pairs = re.findall(
        r'nl-article-title"><a href="([^"]+)".*?<div class="nl-qr">(<svg.*?</svg>)',
        html,
        flags=re.DOTALL,
    )
    assert len(pairs) == 3
    for url, svg in pairs:
        assert _matrix_from_svg(svg) == qr.qr_matrix(url.replace("&amp;", "&"))


def test_preview_escapes_titles(filled_client: FlaskClient) -> None:
    html = html_of(filled_client.get(PREVIEW))
    assert "&lt;script&gt;alert(" in html
    assert "<script>alert" not in html


def test_preview_unknown_or_invalid_month_is_404(client: FlaskClient) -> None:
    assert client.get("/newsletters/2099-01/apercu").status_code == 404
    assert client.get("/newsletters/2099-13/apercu").status_code == 404


def test_preview_of_empty_month_explains(client: FlaskClient, newsletter: Newsletter) -> None:
    response = client.get(PREVIEW)
    assert response.status_code == 200
    assert "encore vide" in html_of(response)


def test_preview_and_print_buttons_on_month_screen(filled_client: FlaskClient) -> None:
    html = html_of(filled_client.get("/newsletters/2026-10/rubrique-1"))
    assert f'href="{PREVIEW}"' in html
    assert f'href="{PREVIEW}?imprimer=1"' in html
    preview = html_of(filled_client.get(PREVIEW))
    assert 'id="imprimer"' in preview and "/static/print.js" in preview
    script = filled_client.get("/static/print.js")
    assert script.status_code == 200 and b"window.print()" in script.data
    # La barre d'aperçu (et son bouton) n'apparaît pas sur la feuille imprimée.
    css = filled_client.get("/static/newsletter.css").get_data(as_text=True)
    assert re.search(r"@media print \{.*\.preview-bar \{ display: none; \}", css, re.DOTALL)


def test_preview_with_invalid_theme(app: Flask, filled_client: FlaskClient, tmp_path: Path) -> None:
    app.config["CP_THEME_FILE"] = write_theme(tmp_path, **{"colors.text": "rouge"})
    response = filled_client.get(PREVIEW)
    assert response.status_code == 500
    assert "colors.text" in html_of(response)
