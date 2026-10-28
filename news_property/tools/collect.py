"""Collecte de la veille en ligne de commande.

Usage : python -m news_property.tools.collect [--month AAAA-MM] [--examples N]

Par défaut, collecte le mois précédent (la newsletter est préparée en début de mois pour
le mois écoulé). Affiche l'avancement puis le bilan.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

from news_property.bootstrap import prepare_database
from news_property.collect.config import RULES_FILE, SOURCES_FILE
from news_property.collect.periods import PARIS, previous_month
from news_property.collect.runner import (
    Clock,
    CollectionAlreadyRunning,
    CollectionReport,
    Progress,
    run_collection,
    system_clock,
)
from news_property.config import ConfigError, Settings, load_settings
from news_property.db import DatabaseError
from news_property.db.connection import get_connection
from news_property.http_client import Fetcher
from news_property.logging_setup import configure_logging
from news_property.version import __version__

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "success": "terminée",
    "partial": "terminée, avec des sources en erreur",
    "failed": "échouée (aucune source n'a pu être lue)",
}

Output = Callable[[str], None]


def parse_args(argv: Sequence[str] | None, default_month: str) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        prog="python -m news_property.tools.collect",
        description="Collecte les articles de veille d'un mois (sources de config/sources.yaml).",
    )
    parser.add_argument(
        "--month",
        default=default_month,
        metavar="AAAA-MM",
        help=f"mois à collecter (défaut : mois précédent, {default_month})",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        metavar="N",
        help="nombre d'exemples d'articles affichés dans le bilan (défaut : 5)",
    )
    return parser.parse_args(argv)


def format_report(report: CollectionReport, examples: int = 5) -> list[str]:
    """Bilan lisible en français (lignes de texte)."""
    lines = [
        "",
        f"Bilan de la collecte {report.month} : {STATUS_LABELS.get(report.status, report.status)}",
        f"  Articles ajoutés, rubrique 1 (immobilier logistique) : {report.added_by_rubric[1]}",
        f"  Articles ajoutés, rubrique 2 (acteurs du marché)     : {report.added_by_rubric[2]}",
        f"  Doublons ignorés          : {report.duplicates}",
        f"  Articles hors période     : {report.out_of_period}",
        f"  Articles sans date        : {report.without_date}",
        f"  Articles hors sujet       : {report.off_topic}",
        f"  Entrées de flux lues      : {report.entries_read}",
        "",
        "Par source configurée (entrées lues -> articles ajoutés) :",
    ]
    lines += [f"  - {s.name} : {s.entries} -> {s.added}" for s in report.sources]
    by_publisher = report.added_by_source_name()
    if by_publisher:
        lines.append("Articles ajoutés par éditeur :")
        lines += [f"  - {name} : {count}" for name, count in by_publisher.items()]
    if report.errors:
        lines += ["", "Sources en erreur :"]
        lines += [f"  - {error}" for error in dict.fromkeys(report.errors)]
    if examples > 0 and report.added_articles:
        lines += ["", f"Exemples ({min(examples, len(report.added_articles))}) :"]
        for article in report.added_articles[:examples]:
            published = (
                article.published_at.astimezone(PARIS).strftime("%d/%m/%Y")
                if article.published_at
                else "-"
            )
            lines += [
                f"  * [{article.rubric}] {article.title}",
                f"    Source : {article.source_name} - {published}",
                f"    Lien : {article.url}",
                f"    Premières lignes : {article.excerpt or '(aucune)'}",
            ]
    return lines


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock = system_clock,
    fetcher: Fetcher | None = None,
    sources_path: Path = SOURCES_FILE,
    rules_path: Path = RULES_FILE,
    out: Output = print,
) -> int:
    """Lance la collecte et affiche le bilan.

    Codes de sortie : 0 collecte terminée (même avec des sources en erreur) ; 1 aucune
    source lue ou erreur inattendue ; 2 configuration invalide, mois invalide ou collecte
    déjà en cours.
    """
    args = parse_args(argv, previous_month(clock()))
    try:
        settings = settings or load_settings()
        configure_logging(settings, console=False)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        prepare_database(settings)
    except (ConfigError, DatabaseError) as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    def on_progress(progress: Progress) -> None:
        if progress.source:
            out(
                f"[{progress.index}/{progress.total}] {progress.source}... "
                f"(articles ajoutés : {progress.added})"
            )

    out(f"Collecte de la veille {args.month} (News Property {__version__})")
    own_fetcher = fetcher is None
    active_fetcher = fetcher or Fetcher()
    try:
        with closing(get_connection(settings)) as conn:
            report = run_collection(
                conn,
                settings,
                args.month,
                clock,
                fetcher=active_fetcher,
                sources_path=sources_path,
                rules_path=rules_path,
                on_progress=on_progress,
            )
    except (ValueError, ConfigError, CollectionAlreadyRunning) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        logger.exception("La collecte a échoué")
        print(
            "La collecte s'est arrêtée sur une erreur inattendue. "
            f"Détail dans le journal : {settings.logs_dir}",
            file=sys.stderr,
        )
        return 1
    finally:
        if own_fetcher:
            active_fetcher.close()

    for line in format_report(report, args.examples):
        out(line)
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
