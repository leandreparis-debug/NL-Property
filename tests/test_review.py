"""Tests de l'interface de relecture (étape 6)."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from flask import Flask
from flask.testing import FlaskClient

from news_property.http_client import Fetcher
from news_property.models import Article, Newsletter
from news_property.repositories import actors, articles, newsletters
from news_property.review.limits import DEFAULT_LIMITS, load_limits
from news_property.review.service import multiline_field
from tests.helpers import html_of, post_form, serve

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
BASE = "/newsletters/2026-10"


@pytest.fixture
def review_app(app: Flask, tmp_path: Path) -> Flask:
    app.config.update(
        CP_CLOCK=lambda: NOW,
        CP_FETCHER_FACTORY=lambda: Fetcher(sleep=lambda _: None),
        CP_LIMITS_FILE=tmp_path / "limites-absent.yaml",
    )
    return app


@pytest.fixture
def web(review_app: Flask) -> FlaskClient:
    return review_app.test_client()


def _add(
    conn: sqlite3.Connection, newsletter: Newsletter, index: int, rubric: int = 1, **kwargs: object
) -> Article:
    assert newsletter.id is not None
    added = articles.add(
        conn,
        Article(
            newsletter_id=newsletter.id,
            rubric=rubric,
            title=kwargs.pop("title", f"Article {index}"),  # type: ignore[arg-type]
            source_name="Business Immo",
            url=f"https://site.example/articles/{index}",
            excerpt="Premières lignes d'origine.",
            published_at=datetime(2026, 10, 5, 8, 30, tzinfo=UTC),
            **kwargs,  # type: ignore[arg-type]
        ),
    )
    assert added is not None
    return added


# --------------------------------------------------------------------------- liste


def test_list_and_create_month(web: FlaskClient, conn: sqlite3.Connection) -> None:
    newsletters.get_or_create(conn, "2026-09")
    reviewed = newsletters.get_or_create(conn, "2026-08")
    newsletters.set_status(conn, reviewed.id, "reviewed")  # type: ignore[arg-type]

    html = html_of(web.get("/newsletters/"))
    assert html.index("Septembre 2026") < html.index("Août 2026")
    assert "Brouillon" in html and "Relue" in html
    assert 'value="2026-10"' in html  # mois précédent proposé

    response = post_form(web, "/newsletters/", {"mois": "2026-10"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"{BASE}/rubrique-1")
    assert newsletters.get(conn, "2026-10") is not None
    assert "Newsletter de Octobre 2026 créée." in html_of(web.get(f"{BASE}/rubrique-1"))
    assert post_form(web, "/newsletters/", {"mois": "2026-13"}).status_code == 302
    assert "Format attendu" in html_of(web.get("/newsletters/"))
    assert web.get("/newsletters/2026-10/").status_code == 302
    assert web.get("/newsletters/2026-12/rubrique-1").status_code == 404
    assert web.get(f"{BASE}/inconnu").status_code == 404


def test_home_review_button(web: FlaskClient) -> None:
    assert 'href="/newsletters/"' in html_of(web.get("/"))


# --------------------------------------------------------------------------- articles


def test_retain_counter_and_limit_warning(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    items = [_add(conn, newsletter, i) for i in range(7)]
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert "<strong>7 retenus</strong> / limite 6" in html
    assert "counter-over" in html and "limite dépassée" in html
    assert "4 pages A4" in html

    post_form(web, f"{BASE}/articles/{items[0].id}/retenir", {"valeur": "0"})
    assert articles.get(conn, items[0].id).selected is False  # type: ignore[union-attr, arg-type]
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert "<strong>6 retenus</strong> / limite 6" in html
    assert "counter-over" not in html
    assert "Article retiré de la newsletter." in html

    retained = html_of(web.get(f"{BASE}/rubrique-1?filtre=non-retenus"))
    assert "Article 0" in retained and "Article 1" not in retained


def test_save_card_ignores_forged_url(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    prologis = actors.get_by_name(conn, "Prologis")
    assert prologis is not None
    response = post_form(
        web,
        f"{BASE}/articles/{article.id}",
        {
            "retenir": "on",
            "titre": "Titre corrigé",
            "extrait": "Premières lignes coupées.",
            "source": "Business Immo",
            "acteur": str(prologis.id),
            "surface": "40 000",
            "date": "2026-10-05",
            "url": "https://pirate.example/faux",
            "lien": "https://pirate.example/faux",
        },
    )
    assert response.status_code == 302
    saved = articles.get(conn, article.id)  # type: ignore[arg-type]
    assert saved is not None
    assert saved.title == "Titre corrigé" and saved.excerpt == "Premières lignes coupées."
    assert saved.url == "https://site.example/articles/1"
    assert saved.actor_id == prologis.id and saved.surface_sqm == 40_000
    assert saved.published_at == article.published_at  # même jour : heure d'origine gardée


def test_other_actor_and_validation_errors(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    form = {
        "titre": "Titre",
        "source": "Source",
        "acteur": "autre",
        "acteur_autre": "Foncière Locale",
    }
    post_form(web, f"{BASE}/articles/{article.id}", form)
    saved = articles.get(conn, article.id)  # type: ignore[arg-type]
    assert saved is not None and saved.actor_label == "Foncière Locale" and saved.actor_id is None

    for bad, field, message in [
        ({**form, "titre": ""}, "titre", "« Titre » est obligatoire"),
        ({**form, "titre": "x" * 301}, "titre", "trop long"),
        ({**form, "date": "2026-02-30"}, "date", "pas une date valide"),
        ({**form, "date": "1500-01-01"}, "date", "année improbable"),
        ({**form, "acteur_autre": ""}, "autre", "Autre acteur"),
        ({**form, "surface": "douze"}, "surface", "nombre entier de m²"),
        ({**form, "acteur": "9999"}, "acteur", "Acteur inconnu"),
    ]:
        response = post_form(web, f"{BASE}/articles/{article.id}", bad)
        html = html_of(response)
        assert response.status_code == 400
        assert re.search(
            rf'id="a{article.id}-{field}-err" role="alert">[^<]*{re.escape(message)}', html
        ), field
    unchanged = articles.get(conn, article.id)  # type: ignore[arg-type]
    assert unchanged is not None and unchanged.title == "Titre"


def test_refused_card_keeps_every_typed_value(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    prologis = actors.get_by_name(conn, "Prologis")
    assert prologis is not None
    response = post_form(
        web,
        f"{BASE}/articles/{article.id}",
        {
            "titre": "",
            "source": "Source corrigée",
            "surface": "12 000",
            "date": "2026-10-07",
            "extrait": "Mon extrait raccourci <b>sans balise</b>.",
            "acteur": str(prologis.id),
            "transaction": "on",
        },
    )
    html = html_of(response)
    assert response.status_code == 400
    card = html[html.index(f'id="article-{article.id}"') :]
    assert 'value="Source corrigée"' in card and 'value="12 000"' in card
    assert 'value="2026-10-07"' in card
    assert "Mon extrait raccourci &lt;b&gt;sans balise&lt;/b&gt;." in card
    assert f'<option value="{prologis.id}" selected>' in card
    assert 'name="transaction" checked' in card
    assert 'value=""' in card.split(f'id="a{article.id}-titre"')[1][:80]
    assert "« Titre » est obligatoire" in card
    assert '<details class="card-details" open>' in card
    assert '<span class="badge badge-unsaved" >Modifications non enregistrées' in card
    assert articles.get(conn, article.id).source_name == "Business Immo"  # type: ignore[union-attr, arg-type]


def test_card_form_has_no_retain_checkbox(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert 'name="retenir"' not in html
    assert "✓ Retenu - ne plus retenir" in html
    # Envoyer « retenir » au formulaire de carte ne change rien : seul le bouton rapide compte.
    articles.set_selected(conn, article.id, False)  # type: ignore[arg-type]
    post_form(web, f"{BASE}/articles/{article.id}", {"titre": "T", "source": "S", "retenir": "on"})
    assert articles.get(conn, article.id).selected is False  # type: ignore[union-attr, arg-type]
    post_form(web, f"{BASE}/articles/{article.id}/retenir", {"valeur": "1"})
    assert articles.get(conn, article.id).selected is True  # type: ignore[union-attr, arg-type]


def test_compact_view_and_unfold_all(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    _add(conn, newsletter, 1, surface_sqm=25_000)
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert '<details class="card-details" >' in html and "<summary>Modifier</summary>" in html
    assert "Tout déplier" in html and "25 000 m²" in html
    unfolded = html_of(web.get(f"{BASE}/rubrique-1?deplier=1"))
    assert '<details class="card-details" open>' in unfolded and "Tout replier" in unfolded


def _order(conn: sqlite3.Connection, newsletter: Newsletter, rubric: int = 1) -> list[str]:
    return [a.title for a in articles.list_for_rubric(conn, newsletter.id, rubric)]  # type: ignore[arg-type]


def test_move_and_keep_first(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    items = [_add(conn, newsletter, i) for i in range(8)]
    post_form(web, f"{BASE}/articles/{items[2].id}/deplacer", {"sens": "haut"})
    post_form(web, f"{BASE}/articles/{items[0].id}/deplacer", {"sens": "bas"})
    order = [a.id for a in articles.list_for_rubric(conn, newsletter.id, 1)]  # type: ignore[arg-type]
    assert order[:3] == [items[2].id, items[0].id, items[1].id]

    response = post_form(web, f"{BASE}/rubrique-1/garder-premiers", {})
    assert response.status_code == 302
    listed = articles.list_for_rubric(conn, newsletter.id, 1)  # type: ignore[arg-type]
    assert [a.selected for a in listed] == [True] * 6 + [False] * 2
    assert "Seuls les 6 premiers articles sont retenus." in html_of(web.get(f"{BASE}/rubrique-1"))
    assert post_form(web, f"{BASE}/inconnu/garder-premiers", {}).status_code == 404


def test_place_first_last_and_rank(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    items = [_add(conn, newsletter, i) for i in range(5)]
    post_form(web, f"{BASE}/articles/{items[4].id}/deplacer", {"sens": "tete"})
    assert _order(conn, newsletter) == [
        "Article 4",
        "Article 0",
        "Article 1",
        "Article 2",
        "Article 3",
    ]
    post_form(web, f"{BASE}/articles/{items[4].id}/deplacer", {"sens": "fin"})
    assert _order(conn, newsletter)[-1] == "Article 4"
    post_form(web, f"{BASE}/articles/{items[4].id}/deplacer", {"sens": "rang", "rang": "2"})
    assert _order(conn, newsletter)[1] == "Article 4"
    post_form(web, f"{BASE}/articles/{items[0].id}/deplacer", {"sens": "rang", "rang": "99"})
    assert _order(conn, newsletter)[-1] == "Article 0"  # borné à la dernière place
    post_form(web, f"{BASE}/articles/{items[0].id}/deplacer", {"sens": "rang", "rang": "-4"})
    assert _order(conn, newsletter)[0] == "Article 0"  # borné à la première place
    before = _order(conn, newsletter)
    post_form(web, f"{BASE}/articles/{items[1].id}/deplacer", {"sens": "rang", "rang": "trois"})
    assert _order(conn, newsletter) == before
    assert "Le rang doit être un numéro" in html_of(web.get(f"{BASE}/rubrique-1"))
    post_form(web, f"{BASE}/articles/{items[0].id}/deplacer", {"sens": "tete"})
    assert "déjà à cette place" in html_of(web.get(f"{BASE}/rubrique-1"))


def test_change_rubric(web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    moving = _add(conn, newsletter, 1, title="Article à reclasser")
    _add(conn, newsletter, 2, rubric=2, title="Déjà en rubrique 2")
    response = post_form(web, f"{BASE}/articles/{moving.id}/rubrique", {"cible": "2", "acteur": ""})
    assert response.status_code == 302
    assert articles.get(conn, moving.id).rubric == 1  # type: ignore[union-attr, arg-type]
    assert "Choisissez l&#39;acteur" in html_of(web.get(f"{BASE}/rubrique-1"))

    geodis = actors.get_by_name(conn, "Geodis")
    assert geodis is not None
    post_form(
        web, f"{BASE}/articles/{moving.id}/rubrique", {"cible": "2", "acteur": str(geodis.id)}
    )
    moved = articles.get(conn, moving.id)  # type: ignore[arg-type]
    assert moved is not None and moved.rubric == 2 and moved.actor_id == geodis.id
    assert _order(conn, newsletter, 2) == ["Déjà en rubrique 2", "Article à reclasser"]
    assert "Article déplacé en fin de Rubrique 2" in html_of(web.get(f"{BASE}/rubrique-1"))

    post_form(web, f"{BASE}/articles/{moving.id}/rubrique", {"cible": "1"})
    back = articles.get(conn, moving.id)  # type: ignore[arg-type]
    assert back is not None and back.rubric == 1 and back.url == moving.url
    assert back.title == "Article à reclasser" and back.selected and back.actor_id == geodis.id

    post_form(
        web,
        f"{BASE}/articles/{moving.id}/rubrique",
        {"cible": "2", "acteur": "autre", "acteur_autre": "Foncière Locale"},
    )
    other = articles.get(conn, moving.id)  # type: ignore[arg-type]
    assert other is not None and other.rubric == 2 and other.actor_label == "Foncière Locale"
    assert (
        post_form(web, f"{BASE}/articles/{moving.id}/rubrique", {"cible": "2"}).status_code == 302
    )
    assert "Rubrique de destination invalide" in html_of(web.get(f"{BASE}/rubrique-2"))


def test_remove_detected_transaction_becomes_manual(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    articles.apply_auto_transaction(
        conn,
        article.id,  # type: ignore[arg-type]
        True,
        "Le logisticien signe un bail pour un entrepôt de 40 000 m².",
        ["signe un bail", "entrepôt"],
        40_000,
    )
    html = html_of(web.get(f"{BASE}/rubrique-1?filtre=transactions"))
    assert "Repère détecté automatiquement" in html and "Article 1" in html
    assert 'class="badge badge-opp">Transaction' in html

    post_form(
        web,
        f"{BASE}/articles/{article.id}",
        {"titre": "Article 1", "source": "Source", "surface": "40000"},
    )
    saved = articles.get(conn, article.id)  # type: ignore[arg-type]
    assert saved is not None and saved.is_transaction is False
    assert saved.transaction_source == "manual"
    assert "réglé à la main" in html_of(web.get(f"{BASE}/rubrique-1"))
    assert "Article 1" not in html_of(web.get(f"{BASE}/rubrique-1?filtre=transactions"))


def test_delete_article_with_confirmation(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    page = html_of(web.get(f"{BASE}/articles/{article.id}/supprimer"))
    assert "Supprimer cet article ?" in page and "Oui, supprimer" in page
    assert articles.get(conn, article.id) is not None  # type: ignore[arg-type]
    post_form(web, f"{BASE}/articles/{article.id}/supprimer", {})
    assert articles.get(conn, article.id) is None  # type: ignore[arg-type]


def test_article_of_another_month_is_404(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    other = _add(conn, newsletters.get_or_create(conn, "2026-09"), 9)
    response = post_form(web, f"{BASE}/articles/{other.id}/retenir", {"valeur": "0"})
    assert response.status_code == 404


# --------------------------------------------------------------------------- ajout manuel

PAGE = """<!doctype html><html><head><title>Titre HTML</title>
<meta property="og:title" content="Un logisticien loue 30 000 m² à Lille">
<meta property="og:site_name" content="Le Journal Exemple">
<meta property="article:published_time" content="2026-10-12T08:00:00+02:00"></head>
<body><nav class="menu"><p>Menu du site avec beaucoup de liens inutiles ici.</p></nav>
<article><p>Le logisticien annonce la location d'un entrepôt de 30 000 m² près de Lille,
livrable l'an prochain.</p></article>
</body></html>"""


def test_manual_article_added_after_link_check(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter, mock_http: respx.MockRouter
) -> None:
    link = "https://journal.example/actu/entrepot?utm_source=mail"
    serve(mock_http, {"https://journal.example/actu/entrepot": httpx.Response(200, text=PAGE)})

    step1 = html_of(web.get(f"{BASE}/ajouter-article?rubrique=2"))
    assert "Vérifier le lien" in step1 and 'value="2" checked' in step1

    step2 = post_form(web, f"{BASE}/ajouter-article/verifier", {"lien": link, "rubrique": "1"})
    html = html_of(step2)
    assert step2.status_code == 200
    assert 'value="Un logisticien loue 30 000 m² à Lille"' in html
    assert 'value="Le Journal Exemple"' in html and 'value="2026-10-12"' in html
    assert "Le logisticien annonce" in html
    assert articles.list_for_newsletter(conn, newsletter.id) == []  # type: ignore[arg-type]

    response = post_form(
        web,
        f"{BASE}/ajouter-article/enregistrer",
        {
            "lien": "https://journal.example/actu/entrepot",
            "rubrique": "1",
            "titre": "Un logisticien loue 30 000 m² à Lille",
            "source": "Le Journal Exemple",
            "extrait": "Le logisticien annonce la location d'un entrepôt de 30 000 m².",
            "date": "2026-10-12",
            "transaction": "on",
            "surface": "30000",
        },
    )
    assert response.status_code == 302
    [added] = articles.list_for_newsletter(conn, newsletter.id)  # type: ignore[arg-type]
    assert added.url == "https://journal.example/actu/entrepot"
    assert added.title == "Un logisticien loue 30 000 m² à Lille" and added.selected
    assert added.body_text is not None and "Lille" in added.body_text
    assert added.is_transaction and added.surface_sqm == 30_000

    again = post_form(web, f"{BASE}/ajouter-article/verifier", {"lien": link, "rubrique": "1"})
    assert again.status_code == 400 and "déjà dans la newsletter" in html_of(again)


@pytest.mark.parametrize(
    ("link", "message"),
    [
        ("https://journal.example/disparu", "Page introuvable (HTTP 404)"),
        ("ftp://journal.example/fichier", "http:// ou https://"),
        ("", "obligatoire"),
    ],
)
def test_manual_article_refused(
    web: FlaskClient,
    conn: sqlite3.Connection,
    newsletter: Newsletter,
    mock_http: respx.MockRouter,
    link: str,
    message: str,
) -> None:
    serve(mock_http, {})
    response = post_form(web, f"{BASE}/ajouter-article/verifier", {"lien": link, "rubrique": "2"})
    assert response.status_code == 400
    assert message in html_of(response)
    forged = post_form(
        web, f"{BASE}/ajouter-article/enregistrer", {"lien": link, "titre": "T", "source": "S"}
    )
    assert forged.status_code == 400
    assert articles.list_for_newsletter(conn, newsletter.id) == []  # type: ignore[arg-type]


ROBOTS_DENY_ALL = httpx.Response(200, text="User-agent: *\nDisallow: /\n")


def test_manual_add_ignores_robots_txt(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter, mock_http: respx.MockRouter
) -> None:
    routes = serve(
        mock_http,
        {
            "https://journal.example/robots.txt": ROBOTS_DENY_ALL,
            "https://journal.example/actu/ferme": httpx.Response(200, text=PAGE),
        },
    )
    step2 = post_form(
        web,
        f"{BASE}/ajouter-article/verifier",
        {"lien": "https://journal.example/actu/ferme", "rubrique": "1"},
    )
    assert step2.status_code == 200
    assert 'value="Un logisticien loue 30 000 m² à Lille"' in html_of(step2)
    assert routes["https://journal.example/robots.txt"].call_count == 0
    saved = post_form(
        web,
        f"{BASE}/ajouter-article/enregistrer",
        {
            "lien": "https://journal.example/actu/ferme",
            "rubrique": "1",
            "titre": "Un logisticien loue",
            "source": "Le Journal Exemple",
        },
    )
    assert saved.status_code == 302
    [added] = articles.list_for_newsletter(conn, newsletter.id)  # type: ignore[arg-type]
    assert added.link_verified_by_user is False


def test_manual_add_site_refusing_robots_needs_confirmation(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter, mock_http: respx.MockRouter
) -> None:
    link = "https://protege.example/article-42"
    serve(mock_http, {link: httpx.Response(403, text="Accès refusé aux robots")})

    step2 = post_form(web, f"{BASE}/ajouter-article/verifier", {"lien": link, "rubrique": "2"})
    html = html_of(step2)
    assert step2.status_code == 200
    assert "Le site refuse la lecture automatique" in html
    assert "J'ai ouvert ce lien dans mon navigateur et il affiche bien l'article" in html
    assert 'name="confirme"' in html

    prologis = actors.get_by_name(conn, "Prologis")
    assert prologis is not None
    form = {
        "lien": link,
        "rubrique": "2",
        "titre": "Prologis livre à Nantes",
        "source": "Le Média Protégé",
        "extrait": "Prologis livre un entrepôt à Nantes.",
        "acteur": str(prologis.id),
    }
    refused = post_form(web, f"{BASE}/ajouter-article/enregistrer", form)
    assert refused.status_code == 400
    assert 'id="confirme-err"' in html_of(refused)
    assert 'value="Prologis livre à Nantes"' in html_of(refused)  # saisie conservée
    assert articles.list_for_newsletter(conn, newsletter.id) == []  # type: ignore[arg-type]

    no_title = post_form(
        web, f"{BASE}/ajouter-article/enregistrer", {**form, "titre": "", "confirme": "on"}
    )
    assert no_title.status_code == 400 and 'id="titre-err"' in html_of(no_title)

    ok = post_form(web, f"{BASE}/ajouter-article/enregistrer", {**form, "confirme": "on"})
    assert ok.status_code == 302
    [added] = articles.list_for_newsletter(conn, newsletter.id)  # type: ignore[arg-type]
    assert added.link_verified_by_user is True and added.url == link
    assert added.rubric == 2 and added.actor_id == prologis.id and added.body_text is None
    assert added.title == "Prologis livre à Nantes" and added.selected
    assert "Lien vérifié par l'utilisateur</span>" in html_of(web.get(f"{BASE}/rubrique-2"))

    # Déjà présent : signalé dès la vérification du lien.
    again = post_form(web, f"{BASE}/ajouter-article/verifier", {"lien": link, "rubrique": "2"})
    assert again.status_code == 400 and "déjà dans la newsletter" in html_of(again)


def test_manual_add_still_refuses_non_http_and_dead_links(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter, mock_http: respx.MockRouter
) -> None:
    serve(mock_http, {})
    for link in ("javascript:alert(1)", "file:///C:/secret.txt", "https://journal.example/absent"):
        response = post_form(
            web,
            f"{BASE}/ajouter-article/enregistrer",
            {"lien": link, "titre": "T", "source": "S", "confirme": "on"},
        )
        assert response.status_code == 400, link
    assert articles.list_for_newsletter(conn, newsletter.id) == []  # type: ignore[arg-type]


# --------------------------------------------------------------------------- statut


def test_mark_reviewed_and_back_to_draft(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    for i in range(7):
        _add(conn, newsletter, i)
    post_form(web, f"{BASE}/statut", {"statut": "relue", "onglet": "rubrique-1"})
    assert newsletters.get(conn, "2026-10").status == "reviewed"  # type: ignore[union-attr]
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert "marquée comme relue" in html
    assert "7 articles retenus pour une limite de 6" in html
    assert "Rubrique 2 - Les acteurs du marché : aucun article retenu" in html
    assert "Repasser en brouillon" in html

    post_form(web, f"{BASE}/statut", {"statut": "brouillon"})
    assert newsletters.get(conn, "2026-10").status == "draft"  # type: ignore[union-attr]
    post_form(web, f"{BASE}/statut", {"statut": "autre"})
    assert "Action inconnue" in html_of(web.get(f"{BASE}/rubrique-1"))


# --------------------------------------------------------------------------- sécurité


def test_site_content_is_escaped(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    _add(conn, newsletter, 1, title="<script>alert('x')</script> Titre piégé")
    html = html_of(web.get(f"{BASE}/rubrique-1"))
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt; Titre piégé" in html
    assert 'rel="noopener noreferrer"' in html


def test_security_unchanged_on_new_routes(
    web: FlaskClient, conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    article = _add(conn, newsletter, 1)
    assert (
        web.post(f"{BASE}/articles/{article.id}/rubrique", data={"cible": "2"}).status_code == 400
    )
    assert (
        web.post(f"{BASE}/articles/{article.id}/deplacer", data={"sens": "tete"}).status_code == 400
    )
    response = web.get(f"{BASE}/rubrique-1", headers={"Host": "evil.example:5000"})
    assert response.status_code == 400


# --------------------------------------------------------------------------- limites


def test_limits_file(tmp_path: Path) -> None:
    assert load_limits(tmp_path / "absent.yaml") == (DEFAULT_LIMITS, None)
    assert DEFAULT_LIMITS.rubrique_1 == 6 and DEFAULT_LIMITS.rubrique_2 == 8
    assert DEFAULT_LIMITS.visites == 4 and DEFAULT_LIMITS.photos_par_visite == 3
    path = tmp_path / "limites.yaml"
    path.write_text("rubrique_1: 3\n", encoding="utf-8")
    limits, error = load_limits(path)
    assert error is None and limits.rubrique_1 == 3 and limits.rubrique_2 == 8
    for text, message in [
        ("rubrique_1: beaucoup\n", "nombre entier"),
        ("rubrique_1: 0\n", "entre 1 et 100"),
        ("rubric_1: 3\n", "clé(s) inconnue(s) rubric_1"),
        ("rubrique_1: [\n", "illisible"),
    ]:
        path.write_text(text, encoding="utf-8")
        limits, error = load_limits(path)
        assert limits == DEFAULT_LIMITS and error is not None and message in error


def test_invalid_limits_message_in_page(
    review_app: Flask, web: FlaskClient, newsletter: Newsletter, tmp_path: Path
) -> None:
    bad = tmp_path / "limites.yaml"
    bad.write_text("rubrique_2: -1\n", encoding="utf-8")
    review_app.config["CP_LIMITS_FILE"] = bad
    html = html_of(web.get(f"{BASE}/rubrique-2"))
    assert "« rubrique_2 » doit être un nombre entier" in html
    assert "/ limite 8" in html


def test_project_limits_file_is_valid() -> None:
    limits, error = load_limits()
    assert error is None and limits == DEFAULT_LIMITS


def test_multiline_field() -> None:
    assert multiline_field({"r": "  a  b \r\n\r\n\r\n c "}, "r", "Résumé", 100) == "a b\n\nc"
    assert multiline_field({"r": " \n "}, "r", "Résumé", 100) is None
    with pytest.raises(ValueError, match="trop long"):
        multiline_field({"r": "x" * 101}, "r", "Résumé", 100)
