"""Tests de la détection : cas étiquetés, règles, enregistrement prudent, outils."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from news_property.config import Settings
from news_property.detect.rules import (
    DETECTION_RULES_FILE,
    DetectionRulesError,
    load_detection_rules,
)
from news_property.detect.runner import run_detection
from news_property.detect.text import article_sentences, split_sentences
from news_property.detect.transaction import SurfaceExtractor, TransactionDetector
from news_property.models import Article, Newsletter
from news_property.repositories import actors, articles
from news_property.tools import detect as detect_tool
from news_property.tools import evaluate_detection
from tests.helpers import FIXTURES_DIR, post_form, web_app

CASES = yaml.safe_load((FIXTURES_DIR / "detection_cases.yaml").read_text(encoding="utf-8"))["cases"]
NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
TITLE = "Le logisticien signe un bail pour un entrepôt de 40 000 m² à Lyon"


@pytest.fixture(scope="module")
def detectors() -> tuple[TransactionDetector, SurfaceExtractor]:
    rules = load_detection_rules()
    return TransactionDetector(rules.transaction), SurfaceExtractor(rules.surface)


# --------------------------------------------------------------------------- cas étiquetés


def test_at_least_15_labelled_cases() -> None:
    assert len(CASES) >= 15
    assert len({case["id"] for case in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_labelled_case(
    case: dict[str, Any], detectors: tuple[TransactionDetector, SurfaceExtractor]
) -> None:
    transaction, surface = detectors
    sentences = split_sentences(case["text"])
    match = transaction.detect(sentences)
    assert (match is not None) == case["transaction"], match
    if match:
        assert match.sentence in sentences  # la preuve est une phrase réelle du texte
        assert match.terms
        found = surface.largest([match.sentence]) or surface.largest(sentences)
    else:
        found = surface.largest(sentences)
    assert found == case["surface"]


def test_split_sentences_keeps_abbreviations_and_newlines() -> None:
    text = "M. Dupont signe. Le bail court sur 9 ans.\nNouvelle ligne"
    assert split_sentences(text) == [
        "M. Dupont signe.",
        "Le bail court sur 9 ans.",
        "Nouvelle ligne",
    ]
    assert article_sentences("Titre", "Extrait. Suite", None) == ["Titre", "Extrait.", "Suite"]
    assert article_sentences("Titre", "Extrait", "Corps. Titre") == ["Titre", "Corps."]


def test_surface_formats(detectors: tuple[TransactionDetector, SurfaceExtractor]) -> None:
    _, surface = detectors
    assert surface.find_all("40 000 m², 12.500 m2 et 3 500 mètres carrés") == [40000, 12500, 3500]
    assert surface.find_all("une surface de 1 200 m² environ") == [1200]
    assert surface.find_all("2 000 000 m² au total") == []  # au-delà du plafond
    assert surface.find_all("en 2026, 30 km de routes") == []


# --------------------------------------------------------------------------- enregistrement


@pytest.fixture
def db(conn: sqlite3.Connection) -> sqlite3.Connection:
    actors.sync_from_yaml(conn)
    return conn


def _article(
    conn: sqlite3.Connection, newsletter: Newsletter, url: str, title: str, body: str = ""
) -> Article:
    assert newsletter.id is not None
    added = articles.add(
        conn,
        Article(
            newsletter_id=newsletter.id,
            rubric=1,
            title=title,
            source_name="Business Immo",
            url=url,
            body_text=body or None,
        ),
    )
    assert added is not None
    return added


def test_detection_saves_evidence_and_surface(
    db: sqlite3.Connection, newsletter: Newsletter
) -> None:
    deal = _article(
        db,
        newsletter,
        "https://a.example/deal",
        "Un logisticien s'agrandit",
        "L'entreprise se porte bien. Elle signe un bail pour un entrepôt de 40 000 m² à Lyon.",
    )
    other = _article(db, newsletter, "https://a.example/rien", "Météo du week-end", "Il fera beau.")
    sized = _article(
        db,
        newsletter,
        "https://a.example/surface",
        "Le marché des entrepôts",
        "Le parc compte déjà 60 000 m² d'entrepôts occupés.",
    )

    report = run_detection(db, "2026-10")

    assert len(report.transactions) == 1 and len(report.surfaces) == 1
    saved = articles.get(db, deal.id)  # type: ignore[arg-type]
    assert saved is not None and saved.is_transaction and saved.transaction_source == "auto"
    assert saved.transaction_evidence == (
        "Elle signe un bail pour un entrepôt de 40 000 m² à Lyon."
    )
    assert saved.transaction_terms == ["signe un bail", "entrepôt", "m2"]
    assert saved.surface_sqm == 40_000
    untouched = articles.get(db, other.id)  # type: ignore[arg-type]
    assert untouched is not None and not untouched.is_transaction
    assert untouched.transaction_terms == [] and untouched.surface_sqm is None
    assert untouched.transaction_source == "auto"  # évalué, sans transaction
    with_surface = articles.get(db, sized.id)  # type: ignore[arg-type]
    assert with_surface is not None and not with_surface.is_transaction
    assert with_surface.surface_sqm == 60_000


def test_rerun_never_touches_user_work(db: sqlite3.Connection, newsletter: Newsletter) -> None:
    deal = _article(db, newsletter, "https://a.example/o", TITLE)
    run_detection(db, "2026-10")

    # Travail de l'utilisateur : repère retiré à la main, surface corrigée.
    articles.set_transaction(db, deal.id, False)  # type: ignore[arg-type]
    current = articles.get(db, deal.id)  # type: ignore[arg-type]
    assert current is not None
    current.surface_sqm = 41_500
    articles.update(db, current)
    before = articles.get(db, deal.id)  # type: ignore[arg-type]

    report = run_detection(db, "2026-10")

    assert articles.get(db, deal.id) == before  # type: ignore[arg-type]
    assert before is not None and not before.is_transaction
    assert before.transaction_source == "manual" and before.surface_sqm == 41_500
    assert report.protected_transactions == 1


def test_known_surface_is_never_replaced(db: sqlite3.Connection, newsletter: Newsletter) -> None:
    deal = _article(db, newsletter, "https://a.example/o", TITLE)
    current = articles.get(db, deal.id)  # type: ignore[arg-type]
    assert current is not None
    current.surface_sqm = 39_000  # saisie à la main avant toute détection
    articles.update(db, current)
    run_detection(db, "2026-10")
    saved = articles.get(db, deal.id)  # type: ignore[arg-type]
    assert saved is not None and saved.is_transaction and saved.surface_sqm == 39_000


def test_manual_flag_via_update(db: sqlite3.Connection, newsletter: Newsletter) -> None:
    plain = _article(db, newsletter, "https://a.example/p", "Météo du week-end")
    plain.is_transaction = True
    articles.update(db, plain)
    run_detection(db, "2026-10")
    saved = articles.get(db, plain.id)  # type: ignore[arg-type]
    assert saved is not None and saved.is_transaction and saved.transaction_source == "manual"


def test_invalid_rules_change_nothing(
    db: sqlite3.Connection, newsletter: Newsletter, tmp_path: Path
) -> None:
    article = _article(db, newsletter, "https://a.example/o", TITLE)
    bad = tmp_path / "regles.yaml"
    bad.write_text("transaction:\n  action_terms: []\n", encoding="utf-8")
    with pytest.raises(DetectionRulesError, match="action_terms"):
        run_detection(db, "2026-10", rules_path=bad)
    text = DETECTION_RULES_FILE.read_text(encoding="utf-8")
    bad.write_text(text.replace("max_sqm: 1000000", "max_sqm: beaucoup"), encoding="utf-8")
    with pytest.raises(DetectionRulesError, match="max_sqm"):
        run_detection(db, "2026-10", rules_path=bad)
    bad.write_text(text.replace("surface_units:", "unites:"), encoding="utf-8")
    with pytest.raises(DetectionRulesError, match="inconnue"):
        run_detection(db, "2026-10", rules_path=bad)
    bad.write_text("transaction: [\n", encoding="utf-8")
    with pytest.raises(DetectionRulesError, match="ligne"):
        run_detection(db, "2026-10", rules_path=bad)
    saved = articles.get(db, article.id)  # type: ignore[arg-type]
    assert saved is not None and saved.transaction_source is None and not saved.is_transaction


def test_yaml_boolean_trap_is_explained(tmp_path: Path) -> None:
    bad = tmp_path / "regles.yaml"
    text = DETECTION_RULES_FILE.read_text(encoding="utf-8").replace("    - BEFA", "    - On")
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(DetectionRulesError, match="entre guillemets"):
        load_detection_rules(bad)


# --------------------------------------------------------------------------- outils


def test_detect_command(settings: Settings, db: sqlite3.Connection, newsletter: Newsletter) -> None:
    _article(db, newsletter, "https://a.example/o", TITLE)
    output: list[str] = []
    code = detect_tool.main(["--month", "2026-10"], settings=settings, out=output.append)
    text = "\n".join(output)
    assert code == 0
    assert "Transactions détectées              : 1" in text
    assert f"Phrase : « {TITLE} »" in text
    assert "Surface : 40 000 m²" in text
    assert detect_tool.main(["--month", "octobre"], settings=settings, out=output.append) == 2


def test_evaluation_report(
    settings: Settings, db: sqlite3.Connection, newsletter: Newsletter
) -> None:
    deal = _article(db, newsletter, "https://a.example/o", TITLE)
    _article(
        db,
        newsletter,
        "https://a.example/miss",
        "Un logisticien signe",
        "Le logisticien signe un bail. L'entrepôt sera prêt en 2027.",
    )
    _article(db, newsletter, "https://a.example/s", "Le parc", "Le parc compte 60 000 m².")
    output: list[str] = []
    code = evaluate_detection.main(
        ["--month", "2026-10"], settings=settings, clock=lambda: NOW, out=output.append
    )
    assert code == 0
    [report_path] = settings.reports_dir.glob("detection-2026-10-*.md")
    text = report_path.read_text(encoding="utf-8")
    assert "## 1. Transactions détectées (1)" in text
    assert f"]({deal.url})" in text
    assert "- [ ] juste  - [ ] faux" in text
    assert "## 2. Surfaces trouvées hors transaction (1)" in text and "60 000 m²" in text
    assert "## 3. Articles non marqués contenant un terme de transaction (2)" in text
    assert "Un logisticien signe" in text and "- [ ] oubli" in text
    # Simulation : la base n'est pas modifiée.
    assert articles.get(db, deal.id).transaction_source is None  # type: ignore[union-attr, arg-type]


# --------------------------------------------------------------------------- intégration


def test_collection_runs_detection(
    settings: Settings, db: sqlite3.Connection, fetcher: Any, mock_http: Any, tmp_path: Path
) -> None:
    from news_property.collect.runner import run_collection
    from news_property.repositories import collection_runs
    from tests.test_collect_runner import FEED_A, _site_routes, write_sources

    _site_routes(mock_http)
    sources = write_sources(tmp_path, [("Site A", FEED_A)])
    report = run_collection(
        db, settings, "2026-10", lambda: NOW, fetcher=fetcher, sources_path=sources
    )
    assert report.detection is not None and report.detection_error is None
    assert report.detection["articles_analysed"] == 3
    run = collection_runs.get(db, report.run_id)
    assert run is not None and run.stats["detection"] == report.detection

    # Règles invalides : la collecte reste valable, l'erreur est signalée.
    bad = tmp_path / "regles.yaml"
    bad.write_text("transaction: {}\n", encoding="utf-8")
    second = run_collection(
        db,
        settings,
        "2026-10",
        lambda: NOW,
        fetcher=fetcher,
        sources_path=sources,
        detection_rules_path=bad,
    )
    assert second.status == "success"
    assert second.detection is None
    assert second.detection_error is not None and "Détection non lancée" in second.detection_error


def test_web_detection_button(
    settings: Settings, db: sqlite3.Connection, newsletter: Newsletter, tmp_path: Path
) -> None:
    _article(db, newsletter, "https://a.example/o", TITLE)
    app = web_app(settings)
    app.config["CP_CLOCK"] = lambda: NOW
    client = app.test_client()
    assert "Relancer la détection" in client.get("/collecte/").get_data(as_text=True)

    response = post_form(client, "/collecte/detection", {"mois": "2026-10"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Transactions détectées</th><td>1" in html
    assert TITLE in html and "40 000 m²" in html

    bad = tmp_path / "regles.yaml"
    bad.write_text("surface: []\n", encoding="utf-8")
    app.config["CP_DETECTION_RULES_FILE"] = bad
    response = post_form(client, "/collecte/detection", {"mois": "2026-10"})
    assert response.status_code == 400
    assert "regles.yaml" in response.get_data(as_text=True)
    assert post_form(client, "/collecte/detection", {"mois": "x"}).status_code == 400
