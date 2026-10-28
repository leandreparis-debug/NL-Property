"""Détection des transactions et des surfaces en ligne de commande.

Usage : python -m news_property.tools.detect [--month AAAA-MM] [--examples N]

Travaille uniquement sur les articles déjà en base (aucun appel réseau).
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
from news_property.detect.reporting import format_summary
from news_property.detect.rules import DETECTION_RULES_FILE
from news_property.detect.runner import run_detection
from news_property.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock = system_clock,
    rules_path: Path = DETECTION_RULES_FILE,
    out: Callable[[str], None] = print,
) -> int:
    """Relance la détection d'un mois et affiche le bilan.

    Codes de sortie : 0 terminé ; 1 erreur inattendue ; 2 règles, configuration ou mois
    invalides (la base n'est alors pas modifiée).
    """
    default_month = previous_month(clock())
    parser = argparse.ArgumentParser(
        prog="python -m news_property.tools.detect",
        description="Détecte les transactions et les surfaces dans les articles d'un mois.",
    )
    parser.add_argument(
        "--month",
        default=default_month,
        metavar="AAAA-MM",
        help=f"mois à analyser (défaut : {default_month})",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        metavar="N",
        help="nombre d'exemples affichés (défaut : 5)",
    )
    args = parser.parse_args(argv)
    try:
        settings = settings or load_settings()
        configure_logging(settings, console=False)
        prepare_database(settings)
        with closing(get_connection(settings)) as conn:
            report = run_detection(conn, args.month, rules_path=rules_path)
    except (ValueError, ConfigError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        logger.exception("La détection a échoué")
        print(
            "La détection s'est arrêtée sur une erreur inattendue (voir le journal).",
            file=sys.stderr,
        )
        return 1
    if report.articles_analysed == 0:
        out(f"Aucun article en base pour {args.month} : lancez d'abord la collecte.")
    for line in format_summary(report, args.examples):
        out(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
