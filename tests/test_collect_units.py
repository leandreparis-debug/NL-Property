"""Tests unitaires du collecteur : texte, URL, périodes, acteurs, classement, extraits."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import feedparser
import pytest

from news_property.actor_catalog import ACTORS_FILE, load_actor_catalog
from news_property.collect.config import (
    RULES_FILE,
    RulesConfigError,
    SourcesConfigError,
    load_rules,
    load_sources,
)
from news_property.collect.excerpt import PageContent, choose_excerpt, extract_page
from news_property.collect.feeds import parse_google_entry, split_google_title
from news_property.collect.matching import ActorMatcher, TermMatcher, classify
from news_property.collect.periods import in_month, month_of, previous_month
from news_property.collect.text import html_to_text, truncate_excerpt
from news_property.collect.urls import clean_url, url_key
from news_property.models import Actor
from news_property.sources.candidates import GOOGLE_NEWS_TOPICS
from tests.helpers import FIXTURES_DIR, fixture_bytes

COLLECT_FIXTURES = FIXTURES_DIR / "collect"


@pytest.fixture(scope="module")
def project_actors() -> list[Actor]:
    """Les acteurs de config/acteurs.yaml, comme en base."""
    return [
        Actor(
            id=index,
            name=entry.name,
            slug=str(index),
            category=entry.category,
            aliases=list(entry.aliases),
            case_sensitive=entry.case_sensitive,
        )
        for index, entry in enumerate(load_actor_catalog(ACTORS_FILE), start=1)
    ]


@pytest.fixture(scope="module")
def matcher(project_actors: list[Actor]) -> ActorMatcher:
    return ActorMatcher(project_actors)


@pytest.fixture(scope="module")
def rules() -> tuple[TermMatcher, TermMatcher]:
    loaded = load_rules(RULES_FILE)
    return TermMatcher(loaded.topic_terms), TermMatcher(loaded.exclusions)


# --------------------------------------------------------------------------- acteurs


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prologis livre un entrepôt à Lyon", "Prologis"),
        ("PROLOGIS accélère", "Prologis"),
        ("Kuehne + Nagel s'installe à Lille", "Kuehne+Nagel"),
        ("KUEHNE+NAGEL ouvre un hub", "Kuehne+Nagel"),
        ("ID LOGISTICS recrute", "ID Logistics"),
        ("Leclerc construit une base", "E.Leclerc"),
        ("Cushman and Wakefield commercialise", "Cushman & Wakefield"),
        ("Les Mousquetaires investissent", "Intermarché"),
        ("Argan signe un bail", "Argan"),
        ("GLP investit en France", "GLP"),
        ("l'huile d'argan fait du bien", None),
        ("le glp est un gaz", None),
        ("un goodman du quartier", None),
        ("l'aew de la semaine", None),
    ],
)
def test_actor_detection(matcher: ActorMatcher, text: str, expected: str | None) -> None:
    found = matcher.find(text)
    assert (found.actor.name if found else None) == expected


def test_untracked_actor_is_ignored() -> None:
    actor = Actor(name="Prologis", slug="prologis", tracked=False)
    assert ActorMatcher([actor]).find("Prologis livre") is None


def test_find_all_in_order_of_appearance(matcher: ActorMatcher) -> None:
    found = matcher.find_all("Geodis loue à Prologis un entrepôt commercialisé par CBRE")
    assert [actor.name for actor in found] == ["Geodis", "Prologis", "CBRE"]


# --------------------------------------------------------------------------- classement


def test_classification(matcher: ActorMatcher, rules: tuple[TermMatcher, TermMatcher]) -> None:
    topics, exclusions = rules
    prologis = classify("Prologis livre son 100e entrepôt", "", matcher, topics, exclusions)
    assert prologis is not None and prologis.rubric == 2 and prologis.actor is not None
    assert prologis.actor.name == "Prologis"
    assert prologis.actor.category == "Investisseur et développeur"

    park = classify("Un nouveau parc logistique dans la Loire", "", matcher, topics, exclusions)
    assert park is not None and park.rubric == 1 and park.actor is None
    assert park.matched == "parc logistique"

    by_summary = classify(
        "Belle rentrée", "Le logisticien FM Logistic s'installe", matcher, topics, exclusions
    )
    assert by_summary is not None and by_summary.rubric == 2

    assert classify("L'entrepôt de données du CHU migre", "", matcher, topics, exclusions) is None
    assert classify("Logistique militaire : un exercice", "", matcher, topics, exclusions) is None
    assert classify("Météo : un week-end ensoleillé", "", matcher, topics, exclusions) is None
    # Exclusion + vrai terme du sujet : reste en rubrique 1.
    mixed = classify(
        "Entrepôt de données et parc logistique : le guide", "", matcher, topics, exclusions
    )
    assert mixed is not None and mixed.rubric == 1


def test_topic_terms_plural_and_accents() -> None:
    terms = TermMatcher(["entrepôt", "s'implante", "plateforme logistique"])
    assert terms.find("Les ENTREPOTS du mois") is not None
    assert terms.find("Le logisticien s’implante à Nice") is not None
    assert terms.find("entreposage frigorifique") is None
    assert terms.find_all("Un entrepôt et une plateforme logistique") == [
        "entrepôt",
        "plateforme logistique",
    ]


# --------------------------------------------------------------------------- périodes


def test_paris_month_boundaries() -> None:
    paris_evening = datetime(2026, 10, 31, 22, 30, tzinfo=UTC)  # 23h30 à Paris (UTC+1)
    utc_evening = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)  # 00h30 le 1er novembre à Paris
    assert in_month(paris_evening, "2026-10")
    assert month_of(utc_evening) == "2026-11"
    assert not in_month(utc_evening, "2026-10")


def test_previous_month() -> None:
    assert previous_month(datetime(2026, 11, 3, tzinfo=UTC)) == "2026-10"
    assert previous_month(datetime(2027, 1, 2, tzinfo=UTC)) == "2026-12"
    # 31/12 23h30 UTC = 1er janvier à Paris → mois précédent : décembre.
    assert previous_month(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == "2026-12"


# --------------------------------------------------------------------------- URL


def test_url_normalization() -> None:
    a = "https://Site.example/articles/entrepot?utm_source=rss&utm_medium=feed&id=3#haut"
    assert clean_url(a) == "https://site.example/articles/entrepot?id=3"
    assert url_key(a) == url_key("https://site.example/articles/entrepot/?id=3&fbclid=x")
    assert url_key("https://www.a.example/x?gclid=1") == url_key("http://a.example/x/")
    assert url_key("https://a.example/x?page=2") != url_key("https://a.example/x?page=3")


# --------------------------------------------------------------------------- texte et extraits


def test_html_to_text_removes_markup() -> None:
    raw = "<p>Bonjour&nbsp;<b>tout</b>\n le   monde</p><script>alert(1)</script><style>p{}</style>"
    assert html_to_text(raw) == "Bonjour tout le monde"
    assert "<" not in html_to_text("&lt;script&gt;alert(1)&lt;/script&gt;")


def test_truncate_excerpt() -> None:
    sentence = "Première phrase assez longue pour compter dans la coupe du texte. "
    text = sentence * 10
    cut = truncate_excerpt(text, 300)
    assert len(cut) <= 303 and cut.endswith("...")
    assert cut.startswith("Première phrase")
    words = " ".join(["mot"] * 200)
    cut_words = truncate_excerpt(words, 300)
    assert cut_words.endswith("mot...") and len(cut_words) <= 303
    assert truncate_excerpt("Court.", 300) == "Court."


@pytest.mark.parametrize(
    ("fixture", "starts_with", "forbidden"),
    [
        (
            "article_cookies_menu.html",
            "Le développeur Prologis a livré",
            ["cookies", "Partager", "injecté", "©"],
        ),
        (
            "article_main_sidebar.html",
            "Porté par un investisseur régional",
            ["newsletter", "plus lus", "droits"],
        ),
        (
            "article_wordpress.html",
            "La foncière Argan",
            ["cookies", "Accueil", "lire aussi", "commentaire"],
        ),
    ],
)
def test_extract_article_body(fixture: str, starts_with: str, forbidden: list[str]) -> None:
    page = extract_page((COLLECT_FIXTURES / fixture).read_text(encoding="utf-8"))
    assert page.body_text.startswith(starts_with)
    excerpt = choose_excerpt(page, "résumé du flux")
    assert excerpt.startswith(starts_with)
    assert len(excerpt) <= 303
    for text in (page.body_text, excerpt):
        assert "<" not in text
        for word in forbidden:
            assert word.lower() not in text.lower()


def test_excerpt_fallbacks() -> None:
    meta_only = extract_page(
        (COLLECT_FIXTURES / "article_meta_only.html").read_text(encoding="utf-8")
    )
    assert meta_only.body_text == ""
    assert choose_excerpt(meta_only, "résumé").startswith(
        "Reportage vidéo : la plateforme logistique de Saint-Vulbas"
    )
    assert choose_excerpt(None, "Résumé du flux RSS.") == "Résumé du flux RSS."
    assert choose_excerpt(PageContent("", ""), "") == ""


# --------------------------------------------------------------------------- Google Actualités


def test_google_title_suffix() -> None:
    assert split_google_title("Prologis livre à Lyon - Le Progrès", "Le Progrès") == (
        "Prologis livre à Lyon",
        "Le Progrès",
    )
    assert split_google_title("Titre - avec tiret - Ouest-France", None) == (
        "Titre - avec tiret",
        "Ouest-France",
    )
    assert split_google_title("Titre sans éditeur", None) == ("Titre sans éditeur", None)


def test_google_entry_parsing() -> None:
    parsed = feedparser.parse(fixture_bytes("google_news.xml"))
    entry = parse_google_entry(parsed.entries[0])
    assert entry is not None
    assert entry.title == "Prologis livre une plateforme logistique à Lyon"
    assert entry.source_name == "Le Progrès"
    assert entry.link.startswith("https://news.google.com/rss/articles/")
    assert entry.published == datetime(2026, 10, 6, 7, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- configuration


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_sources_config(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "sources.yaml",
        "sources:\n"
        "  - {name: A, type: rss, url: 'https://a.example/feed', enabled: true}\n"
        "  - {name: B, type: rss, url: null, enabled: false, verdict: inexploitable}\n"
        "google_news:\n"
        "  enabled: true\n"
        "  query_template: '\"{subject}\" when:30d'\n"
        "  link_resolution: resolved\n"
        "  topics: [immobilier logistique]\n"
        "  subjects: [Prologis]\n",
    )
    config = load_sources(path)
    assert [s.name for s in config.rss] == ["A"]
    assert config.google_news is not None and config.google_news.link_resolution == "resolved"
    assert config.google_news.topics == ("immobilier logistique",)
    without_topics = _write(
        tmp_path,
        "sources2.yaml",
        "google_news:\n  enabled: true\n  query_template: '\"{subject}\" when:30d'\n",
    )
    assert load_sources(without_topics).google_news.topics == GOOGLE_NEWS_TOPICS  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("sources:\n  - {name: A, type: rss, url: null, enabled: true}\n", "sans adresse de flux"),
        ("sources:\n  - {name: A, url: 'https://a.example', enabled: oui}\n", "true ou false"),
        ("google_news: {enabled: true, query_template: 'sans sujet'}\n", "{subject}"),
        (
            "google_news: {enabled: true, query_template: '{subject}', topics: [1]}\n",
            "« topics »",
        ),
        ("sources: []\n", "aucune source activée"),
    ],
)
def test_invalid_sources_config(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(SourcesConfigError, match=message.replace("{", r"\{").replace("}", r"\}")):
        load_sources(_write(tmp_path, "sources.yaml", text))


def test_missing_sources_config_explains_how_to_create(tmp_path: Path) -> None:
    with pytest.raises(SourcesConfigError, match="sources.proposed.yaml"):
        load_sources(tmp_path / "sources.yaml")


def test_rules_config(tmp_path: Path) -> None:
    assert "entrepôt" in load_rules().topic_terms
    assert "entrepôt de données" in load_rules().exclusions
    with pytest.raises(RulesConfigError, match="en double"):
        load_rules(_write(tmp_path, "r.yaml", "topic_terms: [entrepôt, Entrepôt]\n"))
    with pytest.raises(RulesConfigError, match="clé"):
        load_rules(_write(tmp_path, "r.yaml", "topic_terms: [a]\nexclusion: [b]\n"))
    with pytest.raises(RulesConfigError, match="ligne"):
        load_rules(_write(tmp_path, "r.yaml", "topic_terms: [a\n"))


def test_regulation_terms_win_over_actors(
    matcher: ActorMatcher, rules: tuple[TermMatcher, TermMatcher]
) -> None:
    topics, exclusions = rules
    regulation = TermMatcher(["décret", "ICPE", "zéro artificialisation nette"])
    decree = classify(
        "Un décret renforce les règles incendie des entrepôts",
        "",
        matcher,
        topics,
        exclusions,
        regulation,
    )
    assert decree is not None and decree.rubric == 3 and decree.matched == "decret"  # replié
    # Un acteur suivi cité dans un article réglementaire : rubrique 3 quand même.
    mixed = classify(
        "Prologis salue le décret sur les ICPE", "", matcher, topics, exclusions, regulation
    )
    assert mixed is not None and mixed.rubric == 3 and mixed.actor is None
    # Sans terme réglementaire : classement habituel (acteur, puis thème).
    actor = classify("Prologis livre un entrepôt", "", matcher, topics, exclusions, regulation)
    assert actor is not None and actor.rubric == 2
    # Les exclusions s'appliquent avant la recherche des termes réglementaires.
    excluded = TermMatcher(["entrepôts de données", "décret sur"])
    assert (
        classify(
            "Un décret sur les entrepôts de données", "", matcher, topics, excluded, regulation
        )
        is None
    )
