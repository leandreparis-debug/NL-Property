"""Détection d'un mois : analyse des articles en base, puis enregistrement prudent."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from news_property.db.connection import transaction
from news_property.detect.rules import (
    DETECTION_RULES_FILE,
    DetectionRules,
    load_detection_rules,
)
from news_property.detect.text import article_sentences
from news_property.detect.transaction import (
    SurfaceExtractor,
    TransactionDetector,
    TransactionMatch,
)
from news_property.models import Article, validate_month
from news_property.repositories import articles, newsletters

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransactionFinding:
    """Article marqué « transaction » par les règles, avec sa preuve."""

    article: Article
    match: TransactionMatch
    surface_sqm: int | None  # surface trouvée dans la phrase justificative, sinon l'article


@dataclass(frozen=True)
class SurfaceFinding:
    """Surface trouvée dans un article non marqué « transaction »."""

    article: Article
    surface_sqm: int


@dataclass
class DetectionReport:
    """Bilan d'une détection sur un mois."""

    month: str
    articles_analysed: int = 0
    transactions: list[TransactionFinding] = field(default_factory=list)
    surfaces: list[SurfaceFinding] = field(default_factory=list)
    protected_transactions: int = 0  # repères modifiés à la main, laissés tels quels
    dry_run: bool = False

    def summary(self) -> dict[str, Any]:
        """Bilan chiffré (enregistré avec les statistiques de la collecte)."""
        return {
            "articles_analysed": self.articles_analysed,
            "transactions": len(self.transactions),
            "protected_transactions": self.protected_transactions,
            "surfaces": len(self.surfaces)
            + sum(f.surface_sqm is not None for f in self.transactions),
        }


@dataclass(frozen=True)
class Detector:
    """Règles prêtes à l'emploi (détecteurs compilés)."""

    rules: DetectionRules
    transaction: TransactionDetector
    surface: SurfaceExtractor


def build_detector(
    rules: DetectionRules | None = None, *, rules_path: Path = DETECTION_RULES_FILE
) -> Detector:
    """Charge et compile les règles (lève DetectionRulesError si le fichier est invalide)."""
    rules = rules or load_detection_rules(rules_path)
    return Detector(
        rules=rules,
        transaction=TransactionDetector(rules.transaction),
        surface=SurfaceExtractor(rules.surface),
    )


def analyse_month(conn: sqlite3.Connection, month: str, detector: Detector) -> DetectionReport:
    """Applique les règles aux articles du mois, sans rien écrire en base."""
    report = DetectionReport(month=validate_month(month), dry_run=True)
    newsletter = newsletters.get(conn, report.month)
    if newsletter is None or newsletter.id is None:
        return report
    for article in articles.list_for_newsletter(conn, newsletter.id):
        report.articles_analysed += 1
        sentences = article_sentences(article.title, article.excerpt, article.body_text)
        match = detector.transaction.detect(sentences)
        if match:
            surface = detector.surface.largest([match.sentence]) or detector.surface.largest(
                sentences
            )
            report.transactions.append(TransactionFinding(article, match, surface))
            continue
        surface = detector.surface.largest(sentences)
        if surface is not None:
            report.surfaces.append(SurfaceFinding(article, surface))
    return report


def _save(conn: sqlite3.Connection, report: DetectionReport, newsletter_id: int) -> None:
    """Enregistre les détections sans jamais toucher au travail de l'utilisateur."""
    marked = {f.article.id: f for f in report.transactions}
    surfaces = {f.article.id: f.surface_sqm for f in report.surfaces}
    for article in articles.list_for_newsletter(conn, newsletter_id):
        assert article.id is not None
        finding = marked.get(article.id)
        if article.transaction_source == "manual":
            report.protected_transactions += 1
            continue
        articles.apply_auto_transaction(
            conn,
            article.id,
            finding is not None,
            finding.match.sentence if finding else None,
            finding.match.terms if finding else (),
            finding.surface_sqm if finding else surfaces.get(article.id),
        )


def run_detection(
    conn: sqlite3.Connection,
    month: str,
    *,
    dry_run: bool = False,
    rules_path: Path = DETECTION_RULES_FILE,
) -> DetectionReport:
    """Détecte les transactions et les surfaces pour le mois AAAA-MM.

    Les règles sont chargées avant toute écriture : si elles sont invalides,
    DetectionRulesError est levée et la base n'est pas modifiée. L'enregistrement se
    fait dans une seule transaction. Jamais modifié : un repère « transaction » posé ou
    retiré à la main ; une surface déjà connue n'est jamais remplacée.

    dry_run=True : analyse seulement (outil de mesure de fiabilité).
    """
    month = validate_month(month)
    detector = build_detector(rules_path=rules_path)
    report = analyse_month(conn, month, detector)
    report.dry_run = dry_run
    if dry_run:
        return report
    newsletter = newsletters.get(conn, month)
    if newsletter is None or newsletter.id is None:
        return report
    with transaction(conn):
        _save(conn, report, newsletter.id)
    logger.info("Détection %s : %s", month, report.summary())
    return report
