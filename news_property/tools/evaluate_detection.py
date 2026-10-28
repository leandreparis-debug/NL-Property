"""Rapport de fiabilité de la détection, à remplir à la main.

Usage : python -m news_property.tools.evaluate_detection [--month AAAA-MM]

Écrit <données>/reports/detection-AAAA-MM-<horodatage>.md : chaque détection avec sa
phrase, ses termes et une case « juste / faux », puis les articles non marqués qui
contiennent un terme (oublis possibles). La base n'est pas modifiée.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path

from news_property.bootstrap import prepare_database
from news_property.collect.periods import previous_month
from news_property.collect.runner import Clock, system_clock
from news_property.config import ConfigError, Settings, load_settings
from news_property.db import DatabaseError
from news_property.db.connection import get_connection
from news_property.detect.reporting import format_summary, render_evaluation
from news_property.detect.rules import DETECTION_RULES_FILE
from news_property.detect.runner import analyse_month, build_detector
from news_property.logging_setup import configure_logging
from news_property.models import validate_month
from news_property.repositories import articles, newsletters

logger = logging.getLogger(__name__)


def write_evaluation(
    settings: Settings,
    month: str,
    clock: Clock = system_clock,
    *,
    rules_path: Path = DETECTION_RULES_FILE,
) -> tuple[Path, list[str]]:
    """Produit le rapport de fiabilité ; renvoie son chemin et le bilan texte."""
    month = validate_month(month)
    with closing(get_connection(settings)) as conn:
        detector = build_detector(rules_path=rules_path)
        report = analyse_month(conn, month, detector)
        newsletter = newsletters.get(conn, month)
        month_articles = (
            articles.list_for_newsletter(conn, newsletter.id)
            if newsletter is not None and newsletter.id is not None
            else []
        )
    now = clock()
    text = render_evaluation(report, detector.transaction, month_articles, now)
    path = settings.reports_dir / f"detection-{month}-{now.astimezone():%Y%m%d-%H%M}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, format_summary(report, examples=0)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock = system_clock,
    rules_path: Path = DETECTION_RULES_FILE,
    out: Callable[[str], None] = print,
) -> int:
    """Écrit le rapport de fiabilité du mois. Codes : 0 ; 1 erreur ; 2 entrée invalide."""
    default_month = previous_month(clock())
    parser = argparse.ArgumentParser(
        prog="python -m news_property.tools.evaluate_detection",
        description="Rapport de fiabilité de la détection (à remplir à la main).",
    )
    parser.add_argument(
        "--month",
        default=default_month,
        metavar="AAAA-MM",
        help=f"mois à analyser (défaut : {default_month})",
    )
    args = parser.parse_args(argv)
    try:
        settings = settings or load_settings()
        configure_logging(settings, console=False)
        prepare_database(settings)
        path, summary = write_evaluation(settings, args.month, clock, rules_path=rules_path)
    except (ValueError, ConfigError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        logger.exception("Le rapport de fiabilité a échoué")
        print("Le rapport n'a pas pu être produit (voir le journal).", file=sys.stderr)
        return 1
    for line in summary:
        out(line)
    out("")
    out(f"Rapport de fiabilité : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
