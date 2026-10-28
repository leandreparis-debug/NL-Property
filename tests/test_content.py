"""Tests du contenu interne (étape 7) : Une, visites et photos, calendrier, absences, documents."""

from __future__ import annotations

import io
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import respx
from flask.testing import FlaskClient
from markupsafe import escape
from PIL import Image

from news_property.config import Settings
from news_property.content.photos import MAX_SIDE, PhotoError, process_photo
from news_property.content.service import format_period
from news_property.models import Absence, Article, Event, Newsletter, Visit
from news_property.repositories import (
    absences,
    articles,
    document_links,
    events,
    newsletters,
    visits,
)
from tests.helpers import html_of, post_form

BASE = "/newsletters/2026-10"


def image_bytes(fmt: str, size: tuple[int, int] = (2400, 1800), **save: object) -> bytes:
    mode = "RGBA" if fmt == "PNG" else "RGB"
    image = Image.new(mode, size, (30, 120, 200, 128) if mode == "RGBA" else (30, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def jpeg_with_exif(orientation: int = 6) -> bytes:
    image = Image.new("RGB", (3000, 2000), (200, 30, 30))
    exif = image.getexif()
    exif[0x0112] = orientation  # rotation de 90°
    exif[0x8825] = {1: "N", 2: (48.0, 51.0, 24.0), 3: "E", 4: (2.0, 21.0, 3.0)}  # GPS
    exif[0x010F] = "Fabricant"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


@pytest.fixture
def visit(conn: sqlite3.Connection, newsletter: Newsletter) -> Visit:
    assert newsletter.id is not None
    return visits.add(
        conn, Visit(newsletter_id=newsletter.id, site_name="Plateforme Test", location="Lyon")
    )


def upload(client: FlaskClient, visit_id: int, files: list[tuple[bytes, str]]) -> object:
    data = {"photos": [(io.BytesIO(content), name) for content, name in files]}
    return post_form(
        client, f"{BASE}/visites/{visit_id}/photos", data, content_type="multipart/form-data"
    )


# --------------------------------------------------------------------------- onglets et Une


def test_tab_order_and_front_page(
    client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    nid = newsletter.id
    assert nid is not None
    visits.add(conn, Visit(newsletter_id=nid, site_name="Entrepôt A", location="Lyon"))
    for title in ("Comité", "Salon"):
        events.add(conn, Event(newsletter_id=nid, title=title))
    for index in range(3):
        articles.add(
            conn,
            Article(nid, 1, f"A{index}", "S", f"https://a.example/{index}", selected=index < 2),
        )

    html = html_of(client.get(f"{BASE}/une"))
    labels = [
        "Une",
        "1. Immobilier logistique",
        "2. Acteurs du marché",
        "3. Réglementation",
        "4. Visites d'entrepôts",
        "5. Calendrier",
        "6. Congés et absences",
        "7. Documents utiles",
    ]
    nav = html[html.index('aria-label="Rubriques"') :]
    positions = [nav.index(f"\n      {escape(label)}\n") for label in labels]
    assert positions == sorted(positions)
    assert "Ces chiffres se mettent à jour automatiquement" in html
    numbers = re.findall(r'figure-number">(\d+)<', html)
    assert numbers == ["1", "2", "2"]
    assert 'figure-label">visite d&#39;entrepôt<' in html  # singulier
    assert 'figure-label">actualités retenues<' in html
    assert f'href="{BASE}/visites"' in html and f'href="{BASE}/calendrier"' in html
    assert re.search(r"4\. Visites d&#39;entrepôts\s*<span class=\"tab-count\">1/4", html)


def test_home_internal_content_button(client: FlaskClient, conn: sqlite3.Connection) -> None:
    assert 'href="/contenu-interne"' in html_of(client.get("/"))
    response = client.get("/contenu-interne")
    assert response.headers["Location"].endswith("/newsletters/")
    newsletters.get_or_create(conn, "2026-09")
    reviewed = newsletters.get_or_create(conn, "2026-10")
    newsletters.set_status(conn, reviewed.id, "reviewed")  # type: ignore[arg-type]
    response = client.get("/contenu-interne")
    assert response.headers["Location"].endswith("/newsletters/2026-09/visites")


# --------------------------------------------------------------------------- visites


def test_visit_add_edit_move_delete(
    client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter, settings: Settings
) -> None:
    refused = post_form(
        client,
        f"{BASE}/visites/ajouter",
        {"site": "Entrepôt A", "lieu": "", "description": "Texte gardé"},
    )
    assert refused.status_code == 400
    assert 'id="new-lieu-err"' in html_of(refused) and "Texte gardé" in html_of(refused)

    post_form(
        client,
        f"{BASE}/visites/ajouter",
        {"site": "Entrepôt A", "lieu": "Lyon", "date": "2026-10-03", "surface": "40 000"},
    )
    response = post_form(
        client,
        f"{BASE}/visites/ajouter",
        {"site": "Entrepôt B", "lieu": "Nice", "date": "2026-07-01", "acteur": "Prologis"},
    )
    assert response.status_code == 302
    assert "ne tombe ni dans le mois" in html_of(client.get(f"{BASE}/visites"))
    first, second = visits.list_for_newsletter(conn, newsletter.id)  # type: ignore[arg-type]
    assert second.owner_name == "Prologis" and first.surface_sqm == 40_000
    assert 'list="acteurs-suivis"' in html_of(client.get(f"{BASE}/visites"))
    assert "40 000 m²" in html_of(client.get(f"{BASE}/visites"))

    post_form(
        client,
        f"{BASE}/visites/{first.id}",
        {"site": "Entrepôt A bis", "lieu": "Lyon", "description": "x" * 401},
    )
    assert visits.get(conn, first.id).site_name == "Entrepôt A"  # type: ignore[union-attr, arg-type]
    post_form(
        client,
        f"{BASE}/visites/{first.id}",
        {"site": "Entrepôt A bis", "lieu": "Lyon", "description": "Belle visite", "surface": "x"},
    )
    assert visits.get(conn, first.id).site_name == "Entrepôt A"  # type: ignore[union-attr, arg-type]
    post_form(
        client,
        f"{BASE}/visites/{first.id}",
        {"site": "Entrepôt A bis", "lieu": "Lyon", "description": "Belle visite"},
    )
    assert visits.get(conn, first.id).site_name == "Entrepôt A bis"  # type: ignore[union-attr, arg-type]

    post_form(client, f"{BASE}/visites/{second.id}/deplacer", {"sens": "tete"})
    assert [v.id for v in visits.list_for_newsletter(conn, newsletter.id)] == [  # type: ignore[arg-type]
        second.id,
        first.id,
    ]

    upload(client, second.id, [(image_bytes("JPEG"), "a.jpg")])  # type: ignore[arg-type]
    [photo] = visits.list_photos(conn, second.id)  # type: ignore[arg-type]
    photo_path = settings.photos_dir / photo.file_name
    assert photo_path.is_file()
    assert "cette visite" in html_of(client.get(f"{BASE}/visites/{second.id}/supprimer"))
    post_form(client, f"{BASE}/visites/{second.id}/supprimer", {})
    assert visits.get(conn, second.id) is None  # type: ignore[arg-type]
    assert not photo_path.exists()
    assert client.get(f"{BASE}/visites/999/supprimer").status_code == 404


# --------------------------------------------------------------------------- photos


def test_photo_formats_resize_and_limit(
    client: FlaskClient, conn: sqlite3.Connection, visit: Visit, settings: Settings
) -> None:
    response = upload(
        client,
        visit.id,  # type: ignore[arg-type]
        [
            (image_bytes("JPEG"), "facade.jpg"),
            (image_bytes("PNG", (3000, 1000)), "quai.png"),
            (image_bytes("WEBP", (800, 600)), "interieur.webp"),
        ],
    )
    assert response.status_code == 302  # type: ignore[attr-defined]
    photos = visits.list_photos(conn, visit.id)  # type: ignore[arg-type]
    assert len(photos) == 3
    for photo, original in zip(photos, ("facade.jpg", "quai.png", "interieur.webp"), strict=True):
        assert photo.file_name.startswith("2026-10/") and photo.file_name.endswith(".jpg")
        assert original not in photo.file_name
        with Image.open(settings.photos_dir / photo.file_name) as stored:
            assert stored.format == "JPEG" and max(stored.size) <= MAX_SIDE
    with Image.open(settings.photos_dir / photos[0].file_name) as stored:
        assert stored.size == (1600, 1200)
    with Image.open(settings.photos_dir / photos[2].file_name) as stored:
        assert stored.size == (800, 600)  # jamais agrandie

    fourth = upload(client, visit.id, [(image_bytes("JPEG"), "quatre.jpg")])  # type: ignore[arg-type]
    assert fourth.status_code == 302  # type: ignore[attr-defined]
    assert len(visits.list_photos(conn, visit.id)) == 3  # type: ignore[arg-type]
    assert "3 photos au maximum par visite" in html_of(client.get(f"{BASE}/visites"))


def test_fake_and_unsupported_images_are_refused(
    client: FlaskClient, conn: sqlite3.Connection, visit: Visit
) -> None:
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    upload(
        client,
        visit.id,  # type: ignore[arg-type]
        [
            (b"Ceci est un texte, pas une photo.", "piege.jpg"),
            (gif.getvalue(), "anim.gif"),
            (image_bytes("JPEG"), "vraie.jpg"),
        ],
    )
    html = html_of(client.get(f"{BASE}/visites"))
    assert "« piege.jpg » n&#39;est pas une image lisible" in html
    assert "« anim.gif » n&#39;est pas une photo JPEG, PNG ou WebP" in html
    assert len(visits.list_photos(conn, visit.id)) == 1  # type: ignore[arg-type]
    empty = upload(client, visit.id, [])  # type: ignore[arg-type]
    assert empty.status_code == 302  # type: ignore[attr-defined]
    assert "Aucune photo choisie" in html_of(client.get(f"{BASE}/visites"))


def test_upload_over_15_mb_is_refused(client: FlaskClient, visit: Visit) -> None:
    big = b"\xff" * (15 * 1024 * 1024 + 1)
    response = upload(client, visit.id, [(big, "enorme.jpg")])  # type: ignore[arg-type]
    assert response.status_code == 413  # type: ignore[attr-defined]
    assert "dépasse 15 Mo" in html_of(response)


def test_exif_removed_and_orientation_applied(tmp_path: Path) -> None:
    saved = process_photo(jpeg_with_exif(6), tmp_path, "2026-10", "telephone.jpg")
    with Image.open(tmp_path / saved.file_name) as stored:
        assert stored.size == (1067, 1600)  # 3000 × 2000 tourné d'un quart de tour, réduit
        assert not stored.getexif()
        assert "exif" not in stored.info
    raw = (tmp_path / saved.file_name).read_bytes()
    assert b"Exif" not in raw and b"Fabricant" not in raw
    with pytest.raises(PhotoError):
        process_photo(b"", tmp_path, "2026-10", "vide.jpg")


def test_main_photo_caption_and_delete(
    client: FlaskClient, conn: sqlite3.Connection, visit: Visit, settings: Settings
) -> None:
    upload(client, visit.id, [(image_bytes("JPEG"), "1.jpg"), (image_bytes("JPEG"), "2.jpg")])  # type: ignore[arg-type]
    first, second = visits.list_photos(conn, visit.id)  # type: ignore[arg-type]
    html = html_of(client.get(f"{BASE}/visites"))
    assert html.count("★ Photo principale") == 1
    post_form(client, f"{BASE}/photos/{second.id}/principale", {})
    assert [p.id for p in visits.list_photos(conn, visit.id)] == [second.id, first.id]  # type: ignore[arg-type]
    post_form(client, f"{BASE}/photos/{second.id}/legende", {"legende": "Vue des quais"})
    assert visits.get_photo(conn, second.id).caption == "Vue des quais"  # type: ignore[union-attr, arg-type]
    refused = post_form(client, f"{BASE}/photos/{second.id}/legende", {"legende": "x" * 201})
    assert refused.status_code == 302
    assert "Légende non enregistrée" in html_of(client.get(f"{BASE}/visites"))
    path = settings.photos_dir / first.file_name
    assert "cette photo" in html_of(client.get(f"{BASE}/photos/{first.id}/supprimer"))
    post_form(client, f"{BASE}/photos/{first.id}/supprimer", {})
    assert not path.exists() and visits.get_photo(conn, first.id) is None  # type: ignore[arg-type]
    assert client.get(f"{BASE}/photos/999/supprimer").status_code == 404


def test_photo_route_cannot_leave_photos_dir(
    client: FlaskClient, conn: sqlite3.Connection, visit: Visit, settings: Settings
) -> None:
    upload(client, visit.id, [(image_bytes("JPEG"), "a.jpg")])  # type: ignore[arg-type]
    [photo] = visits.list_photos(conn, visit.id)  # type: ignore[arg-type]
    ok = client.get(f"/photos/{photo.file_name}")
    assert ok.status_code == 200 and ok.mimetype == "image/jpeg"
    assert (settings.db_dir / "news_property.sqlite3").is_file()
    secret = settings.data_dir / "secret.jpg"
    secret.write_bytes(b"secret")
    for path in (
        "/photos/../db/news_property.sqlite3",
        "/photos/..%2Fdb%2Fnews_property.sqlite3",
        "/photos/%2e%2e/secret.jpg",
        "/photos/2026-10/../../secret.jpg",
        f"/photos/{settings.data_dir / 'secret.jpg'}",
        "/photos//etc/passwd",
        "/photos/C:%5CWindows%5Cwin.ini",
        "/photos/2026-10/inexistante.jpg",
    ):
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 404, path
        assert b"secret" not in response.data


def test_csrf_required_on_new_forms(
    client: FlaskClient, visit: Visit, conn: sqlite3.Connection
) -> None:
    files = {"photos": [(io.BytesIO(image_bytes("JPEG")), "a.jpg")]}
    assert client.post(f"{BASE}/visites/{visit.id}/photos", data=files).status_code == 400
    for url, data in [
        (f"{BASE}/visites/ajouter", {"site": "S", "lieu": "L"}),
        (f"{BASE}/calendrier/ajouter", {"titre": "Comité"}),
        (f"{BASE}/absences/ajouter", {"nom": "A", "date": "2026-10-01"}),
        (f"{BASE}/documents/enregistrer", {"lien_1": "https://example.com/doc"}),
        (f"{BASE}/documents/retirer", {}),
        (f"{BASE}/visites/{visit.id}/supprimer", {}),
    ]:
        assert client.post(url, data=data).status_code == 400, url
    assert visits.list_photos(conn, visit.id) == []  # type: ignore[arg-type]
    assert events.list_for_newsletter(conn, visit.newsletter_id) == []


# --------------------------------------------------------------------------- calendrier


def test_events(client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    nid = newsletter.id
    assert nid is not None
    post_form(
        client,
        f"{BASE}/calendrier/ajouter",
        {
            "titre": "Salon SITL",
            "date": "2026-11-18",
            "date_fin": "2026-11-20",
            "lieu": "Paris",
            "resume": "Stand B12.\r\n\r\n\r\nRendez-vous à prendre.",
        },
    )
    post_form(client, f"{BASE}/calendrier/ajouter", {"titre": "Point équipe"})
    post_form(client, f"{BASE}/calendrier/ajouter", {"titre": "Comité", "date": "2026-11-05"})
    refused = post_form(client, f"{BASE}/calendrier/ajouter", {"titre": "", "date": "2026-11-05"})
    assert refused.status_code == 400 and 'id="new-titre-err"' in html_of(refused)
    wrong = post_form(
        client,
        f"{BASE}/calendrier/ajouter",
        {"titre": "X", "date": "2026-11-05", "date_fin": "2026-11-01"},
    )
    assert wrong.status_code == 400 and 'id="new-date-fin-err"' in html_of(wrong)
    only_end = post_form(
        client, f"{BASE}/calendrier/ajouter", {"titre": "X", "date_fin": "2026-11-01"}
    )
    assert only_end.status_code == 400 and 'id="new-date-err"' in html_of(only_end)

    listed = events.list_for_newsletter(conn, nid)
    assert [e.title for e in listed] == ["Salon SITL", "Point équipe", "Comité"]
    assert listed[0].summary == "Stand B12.\n\nRendez-vous à prendre."
    html = html_of(client.get(f"{BASE}/calendrier"))
    assert "du 18/11/2026 au 20/11/2026" in html and 'class="meeting-summary"' in html
    assert "<strong>3 rendez-vous</strong> / limite 8" in html

    post_form(client, f"{BASE}/calendrier/trier", {})
    assert [e.title for e in events.list_for_newsletter(conn, nid)] == [
        "Comité",
        "Salon SITL",
        "Point équipe",
    ]
    comite = events.list_for_newsletter(conn, nid)[0]
    post_form(client, f"{BASE}/calendrier/{comite.id}/deplacer", {"sens": "fin"})
    assert events.list_for_newsletter(conn, nid)[-1].title == "Comité"
    post_form(
        client,
        f"{BASE}/calendrier/{comite.id}",
        {"titre": "Comité d'investissement", "date": "2026-11-06", "resume": "x" * 1001},
    )
    assert events.get(conn, comite.id).title == "Comité"  # type: ignore[union-attr, arg-type]
    post_form(
        client, f"{BASE}/calendrier/{comite.id}", {"titre": "Comité d'investissement", "date": ""}
    )
    saved = events.get(conn, comite.id)  # type: ignore[arg-type]
    assert (
        saved is not None and saved.title == "Comité d'investissement" and saved.event_date is None
    )
    assert "cet événement" in html_of(client.get(f"{BASE}/calendrier/{comite.id}/supprimer"))
    post_form(client, f"{BASE}/calendrier/{comite.id}/supprimer", {})
    assert events.get(conn, comite.id) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- absences


def test_absences(client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    nid = newsletter.id
    assert nid is not None
    post_form(
        client,
        f"{BASE}/absences/ajouter",
        {"nom": "Marie Exemple", "date": "2026-11-10", "date_fin": "2026-11-14", "motif": "Congés"},
    )
    post_form(client, f"{BASE}/absences/ajouter", {"nom": "Paul Exemple", "date": "2026-11-03"})
    missing = post_form(client, f"{BASE}/absences/ajouter", {"nom": "X"})
    assert missing.status_code == 400 and 'id="new-date-err"' in html_of(missing)
    wrong = post_form(
        client,
        f"{BASE}/absences/ajouter",
        {"nom": "X", "date": "2026-11-10", "date_fin": "2026-11-01"},
    )
    assert wrong.status_code == 400 and 'id="new-date-fin-err"' in html_of(wrong)

    listed = absences.list_for_newsletter(conn, nid)
    assert [a.person_name for a in listed] == ["Marie Exemple", "Paul Exemple"]
    html = html_of(client.get(f"{BASE}/absences"))
    assert "du 10/11/2026 au 14/11/2026 · Congés" in html and "03/11/2026" in html
    assert "<strong>2 absences</strong> / limite 12" in html

    post_form(client, f"{BASE}/absences/trier", {})
    assert [a.person_name for a in absences.list_for_newsletter(conn, nid)] == [
        "Paul Exemple",
        "Marie Exemple",
    ]
    paul = absences.list_for_newsletter(conn, nid)[0]
    post_form(client, f"{BASE}/absences/{paul.id}/deplacer", {"sens": "fin"})
    assert absences.list_for_newsletter(conn, nid)[-1].person_name == "Paul Exemple"
    post_form(
        client,
        f"{BASE}/absences/{paul.id}",
        {
            "nom": "Paul Exemple",
            "date": "2026-11-03",
            "date_fin": "2026-11-04",
            "motif": "Formation",
        },
    )
    saved = absences.get(conn, paul.id)  # type: ignore[arg-type]
    assert saved is not None and saved.end_date == date(2026, 11, 4) and saved.reason == "Formation"
    assert "cette absence" in html_of(client.get(f"{BASE}/absences/{paul.id}/supprimer"))
    post_form(client, f"{BASE}/absences/{paul.id}/supprimer", {})
    assert absences.get(conn, paul.id) is None  # type: ignore[arg-type]


def test_format_period() -> None:
    assert format_period(None, None) == ""
    assert format_period(date(2026, 11, 3), None) == "03/11/2026"
    assert format_period(date(2026, 11, 3), date(2026, 11, 3)) == "03/11/2026"
    assert format_period(date(2026, 11, 3), date(2026, 11, 5)) == "du 03/11/2026 au 05/11/2026"
    with pytest.raises(ValueError):
        Absence(1, "A", date(2026, 11, 3), date(2026, 11, 1))
    with pytest.raises(ValueError):
        Event(1, "E", date(2026, 11, 3), date(2026, 11, 1))


# --------------------------------------------------------------------------- documents


def test_document_links(
    client: FlaskClient,
    conn: sqlite3.Connection,
    newsletter: Newsletter,
    mock_http: respx.MockRouter,
) -> None:
    assert newsletter.id is not None
    dashboard = "https://carrefour.sharepoint.example/sites/entrepots/tableau-de-bord.xlsx"
    planning = "https://carrefour.sharepoint.example/sites/entrepots/planning.pdf"
    html = html_of(client.get(f"{BASE}/documents"))
    assert html.count('name="lien_') == 6 and html.count('name="libelle_') == 6

    # Lignes 1 et 3 remplies, ligne 2 vide (ignorée) ; libellé vide = libellé par défaut.
    response = post_form(
        client,
        f"{BASE}/documents/enregistrer",
        {"lien_1": dashboard, "libelle_1": "Tableau de bord", "lien_3": planning, "libelle_3": ""},
    )
    assert response.status_code == 302
    assert len(mock_http.calls) == 0  # les liens ne sont jamais appelés
    saved = document_links.list_for_newsletter(conn, newsletter.id)
    assert [(link.position, link.url, link.display_label) for link in saved] == [
        (1, dashboard, "Tableau de bord"),
        (3, planning, "Voir le document"),
    ]
    html = html_of(client.get(f"{BASE}/documents"))
    assert "2 liens dans la newsletter" in html and html.count("Ouvrir ↗") == 2

    for bad in ("javascript:alert(1)", "ftp://serveur/planning.pdf", "planning.pdf"):
        refused = post_form(
            client,
            f"{BASE}/documents/enregistrer",
            {"lien_1": dashboard, "libelle_1": "TB", "lien_4": bad, "libelle_4": "Ouest"},
        )
        assert refused.status_code == 400, bad
        page = html_of(refused)
        assert 'id="doc-lien-4-err"' in page and 'value="Ouest"' in page
    # Libellé sans lien : refusé.
    refused = post_form(client, f"{BASE}/documents/enregistrer", {"libelle_2": "Nord"})
    assert refused.status_code == 400 and 'id="doc-lien-2-err"' in html_of(refused)
    assert len(document_links.list_for_newsletter(conn, newsletter.id)) == 2  # inchangé

    post_form(client, f"{BASE}/documents/retirer", {})
    assert document_links.list_for_newsletter(conn, newsletter.id) == []


# ------------------------------------------------------------------------ statut et suppression


def test_mark_reviewed_new_warnings(
    client: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    post_form(client, f"{BASE}/statut", {"statut": "relue", "onglet": "visites"})
    assert newsletters.get(conn, "2026-10").status == "reviewed"  # type: ignore[union-attr]
    html = html_of(client.get(f"{BASE}/visites"))
    assert "Aucune visite d&#39;entrepôt saisie" in html
    assert "Aucun rendez-vous au calendrier" in html
    assert "Aucune absence saisie" in html
    assert "Pas de lien vers un document utile" in html


def test_delete_newsletter_removes_photo_files(
    client: FlaskClient, conn: sqlite3.Connection, visit: Visit, settings: Settings
) -> None:
    upload(client, visit.id, [(image_bytes("JPEG"), "a.jpg")])  # type: ignore[arg-type]
    [photo] = visits.list_photos(conn, visit.id)  # type: ignore[arg-type]
    path = settings.photos_dir / photo.file_name
    assert path.is_file()
    newsletters.delete(conn, visit.newsletter_id, settings.photos_dir)
    assert not path.exists()
    assert visits.list_for_newsletter(conn, visit.newsletter_id) == []
    assert newsletters.front_page_figures(conn, visit.newsletter_id).visits == 0
