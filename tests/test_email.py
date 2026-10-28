"""Tests de l'étape 9 : e-mail HTML pour Gmail, paquet Apps Script, pages d'export."""

from __future__ import annotations

import base64
import io
import json
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

from news_property.config import ConfigError, Settings, load_settings
from news_property.models import (
    Absence,
    Article,
    DocumentLink,
    Event,
    Newsletter,
    Visit,
    VisitPhoto,
)
from news_property.newsletter import email as email_module
from news_property.newsletter import shapes
from news_property.newsletter.email import (
    GMAIL_CLIP_BYTES,
    MAIN_PHOTO_SIZE,
    PACKAGE_FORMAT,
    THUMB_SIZE,
    build_email,
    package_json,
    write_email_export,
)
from news_property.newsletter.view import EmptyNewsletterError
from news_property.repositories import absences, articles, document_links, events, visits
from tests.helpers import html_of, post_form

PROJECT = Path(__file__).resolve().parent.parent
MONTH = "2026-10"


def _fill(conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings) -> None:
    """Un mois complet : articles (retenus ou non, une transaction, un titre piégé), une
    visite avec deux photos réelles, deux rendez-vous, une absence, deux documents."""
    nid = newsletter.id
    assert nid is not None
    for n, selected in ((1, True), (2, False), (3, True)):
        articles.add(
            conn,
            Article(
                nid,
                1,
                f"Titre {n}",
                "Source test",
                f"https://example.com/a{n}",
                published_at=datetime(2026, 10, n, 8, tzinfo=UTC),
                selected=selected,
                is_transaction=n == 1,
                surface_sqm=40000 if n == 1 else None,
                excerpt="Premières lignes de l'article, copiées telles quelles..."
                if n == 1
                else None,
            ),
        )
    articles.add(
        conn, Article(nid, 2, "<script>alert(1)</script>", "Source", "https://example.com/x")
    )
    folder = settings.photos_dir / MONTH
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 600), (141, 37, 98)).save(folder / "large.jpg")
    Image.new("RGB", (600, 900), (94, 23, 65)).save(folder / "b.jpg")
    visit = visits.add(
        conn,
        Visit(
            nid,
            "Plateforme test",
            "Lyon (69)",
            visit_date=date(2026, 10, 2),
            owner_name="Carrefour Property",
            surface_sqm=42000,
            description="Première ligne.\nSeconde ligne.",
        ),
    )
    assert visit.id is not None
    visits.add_photo(conn, VisitPhoto(visit.id, f"{MONTH}/large.jpg", caption="Façade"))
    visits.add_photo(conn, VisitPhoto(visit.id, f"{MONTH}/b.jpg"))
    events.add(conn, Event(nid, "Comité", event_date=date(2026, 11, 5), location="Massy"))
    events.add(
        conn,
        Event(
            nid,
            "Salon",
            summary=(
                "Un résumé assez long pour prendre toute la largeur de la carte, "
                "sur une seule ligne"
            ),
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


@pytest.fixture
def filled(conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings) -> Settings:
    _fill(conn, newsletter, settings)
    return settings


def _email(app: Flask, conn: sqlite3.Connection, settings: Settings, month: str = MONTH):
    with app.app_context():
        return build_email(conn, month, settings)


# --------------------------------------------------------------------------- e-mail


def test_email_respects_gmail_rules(app: Flask, conn: sqlite3.Connection, filled: Settings) -> None:
    export = _email(app, conn, filled)
    html = export.html
    assert export.subject == "News Property - Octobre 2026"
    # Gmail : pas de SVG, pas de feuille de style ni de mise en page flex/grid, ni script.
    lowered = html.lower()
    for forbidden in ("<svg", "<style", "display:flex", "display:grid", "<script", "position:"):
        assert forbidden not in lowered, forbidden
    # Sous la limite de troncature de Gmail, sans avertissement.
    assert export.html_size < GMAIL_CLIP_BYTES
    assert export.warnings == ()
    # Largeur fixe de 600 px.
    assert 'width="600"' in html


def test_every_image_is_attached_and_used(
    app: Flask, conn: sqlite3.Connection, filled: Settings
) -> None:
    export = _email(app, conn, filled)
    referenced = set(re.findall(r'src="cid:([^"]+)"', export.html))
    attached = {image.cid for image in export.images}
    assert referenced == attached
    assert not re.search(r'src="(?!cid:)', export.html)  # aucune image hébergée ailleurs
    for image in export.images:
        with Image.open(io.BytesIO(image.data)) as opened:
            assert opened.format == ("PNG" if image.mime_type == "image/png" else "JPEG")
    photos = [image for image in export.images if image.cid.startswith("cp-photo")]
    assert len(photos) == 2  # photo principale + vignette
    # Photos recadrées (jamais déformées) aux tailles de l'e-mail.
    with Image.open(io.BytesIO(photos[0].data)) as main_photo:
        assert main_photo.size == MAIN_PHOTO_SIZE
    with Image.open(io.BytesIO(photos[1].data)) as thumb:
        assert thumb.size == THUMB_SIZE


def test_email_links_and_escaping(app: Flask, conn: sqlite3.Connection, filled: Settings) -> None:
    export = _email(app, conn, filled)
    links = set(re.findall(r'href="([^"]+)"', export.html))
    assert links  # liens des articles et des documents, tous réels (http/https)
    assert all(link.startswith(("https://", "http://")) for link in links)
    assert {
        "https://example.com/a1",
        "https://example.com/doc1",
        "https://example.com/doc2",
    } <= links
    assert "https://example.com/a2" not in links  # article non retenu
    assert "&lt;script&gt;alert(" in export.html
    assert "<script>alert" not in export.html


def test_email_content_and_text_version(
    app: Flask, conn: sqlite3.Connection, filled: Settings
) -> None:
    export = _email(app, conn, filled)
    assert "Titre 1" in export.html and "Titre 2" not in export.html
    assert ">Transaction<" in export.html and "40 000 m²" in export.html
    assert "copiées telles quelles..." in export.html
    assert "Plateforme test" in export.html and "Première ligne.<br>Seconde ligne." in export.html
    assert "Visite du 2 octobre 2026" in export.html
    assert "Le 5 novembre 2026" in export.html and 'colspan="2"' in export.html  # résumé large
    assert "Du 2 au 6 novembre 2026" in export.html and "Congés" in export.html
    assert "Tableau de bord →" in export.html and "Voir le document →" in export.html
    # Version texte : titres, liens, repère transaction, contenu interne.
    text = export.text
    assert "Titre 1" in text and "https://example.com/a1" in text and "Titre 2" not in text
    assert "Transaction" in text and "40 000 m²" in text
    assert "- Plateforme test, Lyon (69) - visite du 2 octobre 2026" in text
    assert "- Chloé G. : du 2 au 6 novembre 2026 - Congés" in text
    assert "- Tableau de bord : https://example.com/doc1" in text


def test_empty_newsletter_cannot_be_exported(
    app: Flask, conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    with pytest.raises(EmptyNewsletterError):
        _email(app, conn, settings)


def test_big_html_gives_a_warning(
    app: Flask, conn: sqlite3.Connection, filled: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(email_module, "GMAIL_CLIP_MARGIN", 1000)
    export = _email(app, conn, filled)
    assert len(export.warnings) == 1 and "102 Ko" in export.warnings[0]


# --------------------------------------------------------------------------- paquet


def test_package_round_trip(app: Flask, conn: sqlite3.Connection, filled: Settings) -> None:
    export = _email(app, conn, filled)
    data = json.loads(package_json(export, datetime(2026, 11, 1, 9, tzinfo=UTC)))
    assert data["format"] == PACKAGE_FORMAT
    assert data["month"] == MONTH and data["subject"] == export.subject
    assert data["htmlBody"] == export.html and data["textBody"] == export.text
    assert data["createdAt"] == "2026-11-01T09:00:00+00:00"
    for packed, image in zip(data["images"], export.images, strict=True):
        assert packed["cid"] == image.cid and packed["mimeType"] == image.mime_type
        assert base64.b64decode(packed["data"]) == image.data


def test_write_export_replaces_previous_and_copies_to_drive(
    app: Flask, conn: sqlite3.Connection, filled: Settings, tmp_path: Path
) -> None:
    export = _email(app, conn, filled)
    files = write_email_export(export, filled)
    assert files.html.is_file() and files.package.is_file() and files.drive_copy is None
    assert (files.folder / f"news-property-{MONTH}.eml").is_file()
    stale = files.folder / "images" / "ancienne.png"
    stale.write_bytes(b"x")
    # La version navigateur appelle les images voisines, pas des cid.
    browser = files.html.read_text(encoding="utf-8")
    assert "cid:" not in browser and 'src="images/cp-logo-blanc.png"' in browser

    drive = tmp_path / "drive"
    drive.mkdir()
    files = write_email_export(export, replace(filled, drive_dir=drive))
    assert not stale.exists()
    assert files.drive_copy == drive / f"news-property-{MONTH}.json"
    assert files.drive_copy.read_text(encoding="utf-8") == files.package.read_text(encoding="utf-8")


def test_drive_dir_setting(tmp_path: Path) -> None:
    env = {"CP_DATA_DIR": str(tmp_path / "data"), "CP_OPEN_BROWSER": "false"}
    assert load_settings(env).drive_dir is None
    with pytest.raises(ConfigError, match="CP_DRIVE_DIR"):
        load_settings({**env, "CP_DRIVE_DIR": str(tmp_path / "absent")})
    (tmp_path / "drive").mkdir()
    assert (
        load_settings({**env, "CP_DRIVE_DIR": str(tmp_path / "drive")}).drive_dir
        == (tmp_path / "drive").resolve()
    )


def test_apps_script_matches_package_format() -> None:
    script = (PROJECT / "apps_script" / "brouillon_gmail.gs").read_text(encoding="utf-8")
    assert f"const FORMAT_PAQUET = '{PACKAGE_FORMAT}';" in script
    # Brouillon créé à partir du message MIME complet (API Gmail), jamais envoyé.
    assert "Gmail.Users.Drafts.create({ message: { raw: raw } }, 'me')" in script
    # Lien « voir la version à imprimer » : PDF déposé dans Drive, partagé, lien remplacé.
    assert "deposerPdf_(dossier, paquet.printPdf)" in script
    assert "remplacerLien_(raw, paquet.printLinkToken, lienPdf)" in script
    assert "const PARTAGE_PDF = 'DOMAINE';" in script
    assert ".send(" not in script and "sendEmail" not in script
    assert "\u2014" not in script and "\u2013" not in script  # pas de tirets longs
    manifest = json.loads((PROJECT / "apps_script" / "appsscript.json").read_text("utf-8"))
    services = manifest["dependencies"]["enabledAdvancedServices"]
    assert {"userSymbol": "Gmail", "serviceId": "gmail", "version": "v1"} in services


def test_mime_message_has_inline_images(
    app: Flask, conn: sqlite3.Connection, filled: Settings
) -> None:
    export = _email(app, conn, filled)
    data = json.loads(package_json(export))
    message = message_from_bytes(base64.urlsafe_b64decode(data["raw"]), policy=policy.default)
    assert message["Subject"] == "News Property - Octobre 2026"
    assert message.get_content_type() == "multipart/alternative"
    plain, related = message.get_payload()
    assert plain.get_content_type() == "text/plain"
    # Fins de ligne CRLF : norme des e-mails.
    assert plain.get_content().replace("\r\n", "\n") == export.text
    assert related.get_content_type() == "multipart/related"
    html, *images = related.get_payload()
    assert (
        html.get_content_type() == "text/html"
        and html.get_content().replace("\r\n", "\n").strip() == export.html.strip()
    )
    # Chaque image appelée par cid: dans le HTML est jointe, en ligne, avec son Content-ID.
    by_cid = {part["Content-ID"].strip("<>"): part for part in images}
    assert set(by_cid) == set(re.findall(r'src="cid:([^"]+)"', export.html))
    for image in export.images:
        part = by_cid[image.cid]
        assert part.get_content_disposition() == "inline"
        assert part.get_content_type() == image.mime_type
        assert part.get_content() == image.data
    assert {"cp-photo-1", "cp-photo-2"} <= set(by_cid)  # photos de la visite


def test_shape_png_are_transparent() -> None:
    for kind in shapes.SHAPES:
        with Image.open(io.BytesIO(shapes.shape_png(kind, "#8D2562", 44))) as image:
            assert image.mode == "RGBA" and image.size == (44, 44)
            # Fond transparent autour de la forme, forme opaque.
            assert image.getchannel("A").getextrema() == (0, 255)
    with Image.open(io.BytesIO(shapes.motif_png("#FFFFFF"))) as motif:
        assert motif.size == (420, 450)


# --------------------------------------------------------------------------- pages


def test_export_pages(client: FlaskClient, filled: Settings) -> None:
    html = html_of(client.get("/exports"))
    assert "Octobre 2026" in html and "jamais" in html
    page = client.get(f"/newsletters/{MONTH}/export")
    assert page.status_code == 200 and "Préparer l'e-mail" in html_of(page)
    assert f'href="/newsletters/{MONTH}/export"' in html_of(
        client.get(f"/newsletters/{MONTH}/rubrique-1")
    )
    assert 'href="/exports"' in html_of(client.get("/"))

    assert client.post(f"/newsletters/{MONTH}/export/email").status_code == 400  # sans jeton CSRF
    response = post_form(client, f"/newsletters/{MONTH}/export/email", follow_redirects=True)
    html = html_of(response)
    assert "E-mail préparé" in html and "Dernier e-mail préparé" in html
    assert "pas encore marquée comme relue" in html
    assert f"news-property-{MONTH}.json" in html

    preview = client.get(f"/newsletters/{MONTH}/export/email/apercu")
    assert preview.status_code == 200 and b'src="images/cp-logo.png"' in preview.data
    assert client.get(f"/newsletters/{MONTH}/export/email/images/cp-logo.png").status_code == 200
    package = client.get(f"/newsletters/{MONTH}/export/email/paquet")
    assert package.status_code == 200
    assert "attachment" in package.headers["Content-Disposition"]
    assert json.loads(package.data)["format"] == PACKAGE_FORMAT


@pytest.mark.parametrize(
    "url",
    [
        f"/newsletters/{MONTH}/export/email/images/..%2Fnews-property-{MONTH}.json",
        f"/newsletters/{MONTH}/export/email/images/absente.png",
        f"/newsletters/{MONTH}/export/email/images/cp-logo.svg",
        "/newsletters/2099-01/export",
        "/newsletters/2026-13/export",
    ],
)
def test_export_files_are_protected(client: FlaskClient, filled: Settings, url: str) -> None:
    post_form(client, f"/newsletters/{MONTH}/export/email")
    assert client.get(url).status_code == 404


def test_export_before_preparing(client: FlaskClient, filled: Settings) -> None:
    assert client.get(f"/newsletters/{MONTH}/export/email/apercu").status_code == 404
    assert client.get(f"/newsletters/{MONTH}/export/email/paquet").status_code == 404


def test_export_of_empty_month_explains(client: FlaskClient, newsletter: Newsletter) -> None:
    html = html_of(post_form(client, f"/newsletters/{MONTH}/export/email", follow_redirects=True))
    assert "encore vide" in html
    assert "Dernier e-mail préparé" not in html


# --------------------------------------------------------------------------- lien PDF

FAKE_PDF = b"%PDF-1.4 version de test"


def test_print_link_points_to_the_pdf(
    app: Flask, conn: sqlite3.Connection, filled: Settings
) -> None:
    with app.app_context():
        export = build_email(conn, MONTH, filled, make_print_pdf=lambda v, t: FAKE_PDF)
    token = email_module.PRINT_LINK_TOKEN
    assert export.print_pdf == FAKE_PDF and export.warnings == ()
    # Lien sous le bandeau et en pied de page, vers le lien provisoire (remplacé par le script).
    assert export.html.count(f'href="{token}"') == 2
    assert "de Carrefour Property - <a" in export.html
    assert "voir la version à imprimer</a>" in export.html
    assert f"Voir la version à imprimer (PDF) : {token}" in export.text
    # Version navigateur : le lien ouvre le PDF voisin.
    assert export.browser_html.count(f'href="news-property-{MONTH}.pdf"') == 2

    data = json.loads(package_json(export))
    assert data["printLinkToken"] == token
    assert data["printPdf"]["fileName"] == f"news-property-{MONTH}.pdf"
    assert base64.b64decode(data["printPdf"]["data"]) == FAKE_PDF
    # Texte et HTML lisibles tels quels dans le message (8bit) : le script peut remplacer le
    # lien provisoire ; lignes de moins de 998 octets (norme des e-mails).
    raw = base64.urlsafe_b64decode(data["raw"])
    assert raw.count(token.encode()) == 3
    assert max(len(line) for line in raw.split(b"\r\n")) <= 998
    message = message_from_bytes(raw, policy=policy.default)
    for part in message.walk():
        if part.get_content_maintype() == "text":
            assert part["Content-Transfer-Encoding"] == "8bit"
    # Remplacement tel que le fait le script : le message reste valide, images comprises.
    link = "https://drive.google.com/file/d/abc/view?usp=drivesdk"
    fixed = message_from_bytes(raw.replace(token.encode(), link.encode()), policy=policy.default)
    html_part = next(p for p in fixed.walk() if p.get_content_type() == "text/html")
    assert html_part.get_content().count(f'href="{link}"') == 2
    images = [p for p in fixed.walk() if p.get_content_maintype() == "image"]
    assert {p.get_content() for p in images} == {i.data for i in export.images}


def test_no_print_link_without_pdf(app: Flask, conn: sqlite3.Connection, filled: Settings) -> None:
    from news_property.newsletter.pdf import PdfError

    def failing(view: object, theme: object) -> bytes:
        raise PdfError("Aucun navigateur Edge ou Chrome trouvé.")

    with app.app_context():
        export = build_email(conn, MONTH, filled, make_print_pdf=failing)
        plain = build_email(conn, MONTH, filled)
    for result in (export, plain):
        assert result.print_pdf is None
        assert email_module.PRINT_LINK_TOKEN not in result.html + result.text
        assert json.loads(package_json(result))["printPdf"] is None
    assert len(export.warnings) == 1
    assert "sans lien vers la version à imprimer" in export.warnings[0]


def test_export_page_with_print_pdf(
    client: FlaskClient, filled: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from news_property.newsletter import views

    monkeypatch.setattr(views, "newsletter_pdf", lambda view, theme, settings: FAKE_PDF)
    html = html_of(post_form(client, f"/newsletters/{MONTH}/export/email", follow_redirects=True))
    assert f"news-property-{MONTH}.pdf" in html and "voir la version à imprimer" in html
    pdf = client.get(f"/newsletters/{MONTH}/export/email/news-property-{MONTH}.pdf")
    assert pdf.status_code == 200 and pdf.data == FAKE_PDF
    assert pdf.mimetype == "application/pdf"
    assert client.get(f"/newsletters/{MONTH}/export/email/autre.pdf").status_code == 404
    folder = filled.exports_dir / MONTH / "email"
    assert (folder / f"news-property-{MONTH}.pdf").read_bytes() == FAKE_PDF
