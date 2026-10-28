"""Tests d'intégration de la collecte (sources simulées avec respx, horloge injectée)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from news_property.collect.config import SourcesConfigError
from news_property.collect.runner import CollectionAlreadyRunning, run_collection
from news_property.config import Settings
from news_property.http_client import Fetcher
from news_property.repositories import actors, articles, collection_runs, newsletters
from news_property.sources.candidates import GOOGLE_NEWS_TOPICS
from news_property.sources.evaluation import build_google_news_url
from tests.helpers import FIXTURES_DIR, fixture_bytes, rss, serve

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
MONTH = "2026-10"
FEED_A = "https://site-a.example/feed"
FEED_B = "https://site-b.example/feed"
FEED_C = "https://site-c.example/feed"
GOOGLE_SEARCH = "https://news.google.com/rss/search"


def clock() -> datetime:
    return NOW


def collect_page(name: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=(FIXTURES_DIR / "collect" / name).read_bytes(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def collect_feed(name: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=(FIXTURES_DIR / "collect" / name).read_bytes(),
        headers={"content-type": "application/rss+xml"},
    )


ARTICLE_PAGES = {
    "https://site-a.example/articles/prologis-100e-entrepot": "article_cookies_menu.html",
    "https://site-a.example/articles/nouveau-parc-loire": "article_main_sidebar.html",
    "https://site-a.example/articles/halloween-paris": "article_meta_only.html",
    "https://site-b.example/news/argan-10-entrepots": "article_wordpress.html",
}


def write_sources(
    tmp_path: Path,
    rss_sources: list[tuple[str, str]],
    google: str | None = None,
) -> Path:
    lines = ["sources:"]
    for name, url in rss_sources:
        lines.append(f"  - {{name: '{name}', type: rss, url: '{url}', enabled: true}}")
    if not rss_sources:
        lines[0] = "sources: []"
    if google:
        lines += [
            "google_news:",
            "  enabled: true",
            "  query_template: '\"{subject}\" when:30d'",
            f"  link_resolution: {google}",
        ]
    path = tmp_path / "sources.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def db(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Base migrée avec les acteurs du référentiel."""
    actors.sync_from_yaml(conn)
    return conn


def _site_routes(
    router: respx.MockRouter, feed_a: str = "feed_site_a.xml"
) -> dict[str, respx.Route]:
    routes = {FEED_A: collect_feed(feed_a), FEED_B: collect_feed("feed_site_b.xml")}
    routes.update({url: collect_page(page) for url, page in ARTICLE_PAGES.items()})
    return serve(router, routes)


def _run(db: sqlite3.Connection, settings: Settings, fetcher: Fetcher, sources: Path):  # noqa: ANN202
    return run_collection(db, settings, MONTH, clock, fetcher=fetcher, sources_path=sources)


def test_full_collection_two_sites(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    _site_routes(mock_http)
    sources = write_sources(tmp_path, [("Site A", FEED_A), ("Site B", FEED_B)])

    report = _run(db, settings, fetcher, sources)

    assert report.status == "success", report.errors
    assert report.added_by_rubric == {1: 2, 2: 2}
    assert report.duplicates == 2  # même URL normalisée + titre quasi identique
    assert report.out_of_period == 2  # septembre + 31/10 23h30 UTC (= novembre à Paris)
    assert report.without_date == 1
    assert report.off_topic == 3  # entrepôt de données, météo, « huile d'argan »
    assert report.entries_read == 12

    nid = report.newsletter_id
    rubric2 = articles.list_for_rubric(db, nid, 2)
    assert [a.title for a in rubric2] == [
        "Prologis livre son 100e entrepôt en France",
        "Argan livre 10 entrepôts cet automne",
    ]
    prologis = rubric2[0]
    prologis_actor = actors.get_by_name(db, "Prologis")
    assert prologis_actor is not None and prologis.actor_id == prologis_actor.id
    assert prologis.url == "https://site-a.example/articles/prologis-100e-entrepot"  # sans utm_*
    assert prologis.source_name == "Site A"
    assert prologis.excerpt is not None
    assert prologis.excerpt.startswith("Le développeur Prologis a livré")
    assert prologis.body_text is not None and "trentaine de livraisons" in prologis.body_text
    assert prologis.selected is True and prologis.position == 0
    assert prologis.collected_at == NOW
    # Détection lancée en fin de collecte : « livre » + « entrepôt » dans le titre.
    assert prologis.is_transaction is True and prologis.transaction_source == "auto"
    assert prologis.transaction_evidence == "Prologis livre son 100e entrepôt en France"
    assert report.detection is not None and report.detection["transactions"] >= 1

    rubric1 = articles.list_for_rubric(db, nid, 1)
    assert [a.title for a in rubric1] == [
        "Un nouveau parc logistique dans la Loire",
        "Plateforme logistique : un bail signé le soir d'Halloween",
    ]
    assert all(a.actor_id is None for a in rubric1)
    assert rubric1[1].excerpt == (
        "Reportage vidéo : la plateforme logistique de Saint-Vulbas entre en service."
    )

    for article in rubric1 + rubric2:
        for text in (article.title, article.excerpt or "", article.body_text or ""):
            assert "<" not in text and "alert" not in text

    run = collection_runs.last_for_newsletter(db, nid)
    assert run is not None and run.status == "success"
    assert run.started_at == NOW and run.finished_at == NOW
    assert run.stats["added_rubric_1"] == 2 and run.stats["added_rubric_2"] == 2
    assert run.stats["added_by_source_name"] == {"Site A": 3, "Site B": 1}
    assert run.errors == []


def test_rss_summary_used_when_page_unavailable(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    serve(mock_http, {FEED_A: collect_feed("feed_site_a.xml")})  # pages d'articles : 404
    report = _run(db, settings, fetcher, write_sources(tmp_path, [("Site A", FEED_A)]))
    prologis = articles.list_for_rubric(db, report.newsletter_id, 2)[0]
    assert prologis.excerpt == "Le développeur accélère."
    assert prologis.body_text is None


def test_source_timeout_gives_partial(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    mock_http.get(FEED_C).mock(side_effect=httpx.ConnectTimeout("trop long"))
    _site_routes(mock_http)
    sources = write_sources(tmp_path, [("Site C", FEED_C), ("Site A", FEED_A)])

    report = _run(db, settings, fetcher, sources)

    assert report.status == "partial"
    assert report.added_total == 3
    assert report.errors == ["Site C : Le site n'a pas répondu à temps (délai de 15 s dépassé)."]
    run = collection_runs.get(db, report.run_id)
    assert run is not None and run.status == "partial" and run.errors == report.errors


def test_all_sources_failing_gives_failed(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    serve(mock_http, {})
    report = _run(db, settings, fetcher, write_sources(tmp_path, [("Site A", FEED_A)]))
    assert report.status == "failed"
    assert report.errors == ["Site A : Page introuvable (HTTP 404)."]


def test_rerun_keeps_review_work(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    _site_routes(mock_http)
    sources = write_sources(tmp_path, [("Site A", FEED_A)])
    first = _run(db, settings, fetcher, sources)
    nid = first.newsletter_id
    prologis = articles.list_for_rubric(db, nid, 2)[0]
    assert prologis.id is not None
    prologis.title = "Titre corrigé par l'utilisateur"
    prologis.excerpt = "Extrait corrigé."
    articles.update(db, prologis)
    articles.set_selected(db, prologis.id, False)
    before = {a.id: a for r in (1, 2) for a in articles.list_for_rubric(db, nid, r)}

    # Le flux s'enrichit d'un nouvel article entre les deux collectes.
    mock_http.reset()
    new_item = (
        "<item><title>Un nouvel entrepôt frigorifique à Rungis</title>"
        "<link>https://site-a.example/articles/rungis</link>"
        "<pubDate>Thu, 29 Oct 2026 10:00:00 +0100</pubDate></item></channel>"
    )
    feed = fixture_bytes("collect/feed_site_a.xml").decode().replace("</channel>", new_item)
    mock_http.routes.clear()
    serve(mock_http, {FEED_A: httpx.Response(200, text=feed)})

    second = _run(db, settings, fetcher, sources)

    assert second.added_total == 1
    assert second.added_articles[0].title == "Un nouvel entrepôt frigorifique à Rungis"
    after = {a.id: a for r in (1, 2) for a in articles.list_for_rubric(db, nid, r)}
    for article_id, article in before.items():
        # Rien n'est modifié, sauf la preuve de la détection automatique, recalculée
        # (le titre corrigé ne contient plus la phrase : la preuve vient du texte).
        kept = {k: v for k, v in vars(article).items() if not k.startswith("transaction_")}
        assert {k: v for k, v in vars(after[article_id]).items() if k in kept} == kept
    corrected = after[prologis.id]
    assert corrected.title == "Titre corrigé par l'utilisateur"
    assert corrected.selected is False
    assert second.duplicates == 3  # les 3 articles déjà présents
    assert len(collection_runs.list_running(db)) == 0


def test_google_news_link_only(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    articles_route = mock_http.get(url__startswith="https://news.google.com/rss/articles/")
    mock_http.get(build_google_news_url("Prologis")).mock(return_value=rss("google_news.xml"))
    searches = mock_http.get(url__startswith=GOOGLE_SEARCH).mock(
        return_value=rss("google_news_empty.xml")
    )
    serve(mock_http, {})

    report = _run(db, settings, fetcher, write_sources(tmp_path, [], google="google_link_only"))

    assert report.status == "success", report.errors
    assert report.added_by_rubric == {1: 0, 2: 1}
    assert report.out_of_period == 2
    # Une recherche par thème, puis une par acteur suivi.
    tracked = len(actors.list_all(db, tracked_only=True))
    assert searches.call_count + 1 == len(GOOGLE_NEWS_TOPICS) + tracked
    article = report.added_articles[0]
    assert article.title == "Prologis livre une plateforme logistique à Lyon"
    assert article.source_name == "Le Progrès"
    assert article.url == "https://news.google.com/rss/articles/CBMiTESTARTICLE1?oc=5"
    assert article.excerpt is None
    assert articles_route.call_count == 0  # aucun appel aux liens en mode google_link_only
    assert len(report.sources) == 1 and report.sources[0].kind == "google_news"


def test_google_news_resolution(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    publisher = "https://www.leprogres.example/economie/prologis-lyon"
    google_link = "https://news.google.com/rss/articles/CBMiTESTARTICLE1?oc=5"
    mock_http.get(google_link).mock(
        return_value=httpx.Response(302, headers={"location": publisher})
    )
    mock_http.get(publisher).mock(return_value=collect_page("article_cookies_menu.html"))
    mock_http.get(build_google_news_url("Prologis")).mock(return_value=rss("google_news.xml"))
    mock_http.get(url__startswith=GOOGLE_SEARCH).mock(return_value=rss("google_news_empty.xml"))
    serve(mock_http, {})

    report = _run(db, settings, fetcher, write_sources(tmp_path, [], google="resolved"))

    article = report.added_articles[0]
    assert article.url == publisher
    assert article.excerpt is not None
    assert article.excerpt.startswith("Le développeur Prologis a livré")


def test_google_news_resolution_failure_keeps_google_link(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    mock_http.get(url__startswith="https://news.google.com/rss/articles/").mock(
        return_value=httpx.Response(200, text="<html>redirection JavaScript</html>")
    )
    mock_http.get(build_google_news_url("Prologis")).mock(return_value=rss("google_news.xml"))
    mock_http.get(url__startswith=GOOGLE_SEARCH).mock(return_value=rss("google_news_empty.xml"))
    serve(mock_http, {})

    report = _run(db, settings, fetcher, write_sources(tmp_path, [], google="resolved"))

    article = report.added_articles[0]
    assert article.url == "https://news.google.com/rss/articles/CBMiTESTARTICLE1?oc=5"
    assert article.excerpt is None


def test_lock_refuses_second_collection(
    db: sqlite3.Connection,
    settings: Settings,
    fetcher: Fetcher,
    mock_http: respx.MockRouter,
    tmp_path: Path,
) -> None:
    _site_routes(mock_http)
    sources = write_sources(tmp_path, [("Site A", FEED_A)])
    newsletter = newsletters.get_or_create(db, MONTH)
    assert newsletter.id is not None
    running = collection_runs.start(db, newsletter.id, NOW - timedelta(minutes=5))

    with pytest.raises(CollectionAlreadyRunning, match="déjà en cours"):
        _run(db, settings, fetcher, sources)
    assert articles.list_for_rubric(db, newsletter.id, 1) == []

    # Collecte « running » trop ancienne (application fermée brutalement) : on reprend.
    db.execute(
        "UPDATE collection_runs SET started_at = ? WHERE id = ?",
        ((NOW - timedelta(hours=5)).isoformat(), running.id),
    )
    report = _run(db, settings, fetcher, sources)
    assert report.status == "success"
    stale = collection_runs.get(db, running.id)  # type: ignore[arg-type]
    assert stale is not None and stale.status == "failed"
    assert "Collecte interrompue." in stale.errors


def test_missing_sources_file_starts_nothing(
    db: sqlite3.Connection, settings: Settings, fetcher: Fetcher, tmp_path: Path
) -> None:
    with pytest.raises(SourcesConfigError, match="sources.proposed.yaml"):
        _run(db, settings, fetcher, tmp_path / "absent.yaml")
    assert newsletters.list_all(db) == []


def test_invalid_month(
    db: sqlite3.Connection, settings: Settings, fetcher: Fetcher, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="AAAA-MM"):
        run_collection(db, settings, "octobre", clock, fetcher=fetcher)
