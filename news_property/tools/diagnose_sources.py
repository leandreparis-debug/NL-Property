"""Diagnostic des sources de veille.

Usage : python -m news_property.tools.diagnose_sources [--only <nom>] [--no-google] [--open]

Teste chaque source candidate, puis écrit dans <dossier de données>/reports :
- sources-AAAAMMJJ-HHMM.md : rapport lisible en français ;
- sources-AAAAMMJJ-HHMM.json : mêmes données structurées ;
- sources.proposed.yaml : proposition de configuration à valider par l'utilisateur.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from news_property.collect.config import SOURCES_FILE, SourcesConfigError, load_sources
from news_property.config import ConfigError, Settings, load_settings
from news_property.http_client import Fetcher
from news_property.logging_setup import configure_logging
from news_property.sources.candidates import (
    CANDIDATE_SITES,
    GOOGLE_NEWS_NAME,
    GOOGLE_NEWS_QUERY_TEMPLATE,
    CandidateSite,
    google_news_subjects,
)
from news_property.sources.discovery import FeedAttempt, discover_feeds_detailed
from news_property.sources.evaluation import (
    EXPLOITABLE,
    GOOGLE_LINK_ONLY,
    PARTIAL,
    RESOLVED,
    UNUSABLE,
    VERDICT_RANK,
    FeedReport,
    evaluate_feed,
    evaluate_google_news,
)
from news_property.version import __version__

logger = logging.getLogger(__name__)

MAX_FEEDS_PER_SITE = 3
UNEXPECTED_ERROR = "Erreur inattendue pendant le diagnostic ({}) : détail dans le journal."

Progress = Callable[[str], None]


@dataclass
class SourceResult:
    """Résultat du diagnostic d'une source (site ou Google Actualités)."""

    name: str
    type: str  # "rss" ou "google_news"
    verdict: str = UNUSABLE
    reason: str = ""
    site_url: str | None = None
    best_feed: str | None = None
    feeds_found: list[str] = field(default_factory=list)
    homepage_error: str | None = None
    discovery_attempts: list[FeedAttempt] = field(default_factory=list)
    reports: list[FeedReport] = field(default_factory=list)
    link_resolution: str | None = None


@dataclass
class Diagnosis:
    """Ensemble du diagnostic."""

    generated_at: datetime
    version: str
    sources: list[SourceResult] = field(default_factory=list)


# --------------------------------------------------------------------------- diagnostic


def _report_sort_key(report: FeedReport) -> tuple[int, int, int]:
    """Clé de tri : meilleur verdict, puis plus d'articles récents, puis plus d'articles."""
    return (VERDICT_RANK[report.verdict], report.recent_count, report.entry_count)


def diagnose_site(site: CandidateSite, fetcher: Fetcher, now: datetime) -> SourceResult:
    """Diagnostique un site : découverte des flux puis évaluation des meilleurs.

    Ne lève jamais d'exception : une erreur inattendue donne un verdict « inexploitable ».
    """
    result = SourceResult(name=site.name, type="rss", site_url=site.site_url)
    try:
        discovery = discover_feeds_detailed(site.site_url, fetcher)
        result.feeds_found = discovery.feeds
        result.homepage_error = discovery.homepage_error
        result.discovery_attempts = discovery.attempts
        if not discovery.feeds:
            result.verdict = UNUSABLE
            result.reason = "Aucun flux RSS/Atom trouvé."
            if discovery.homepage_error:
                result.reason += f" Page d'accueil : {discovery.homepage_error}"
            return result

        # Les flux de commentaires (WordPress) ne sont pas des flux d'actualité.
        feeds = [f for f in discovery.feeds if "comment" not in f.lower()] or discovery.feeds
        for feed_url in feeds[:MAX_FEEDS_PER_SITE]:
            result.reports.append(evaluate_feed(feed_url, fetcher=fetcher, now=now))
        best = max(result.reports, key=_report_sort_key)
        result.best_feed = best.feed_url
        result.verdict, result.reason = best.verdict, best.reason
    except Exception as exc:
        logger.exception("Diagnostic de %s interrompu", site.name)
        result.verdict = UNUSABLE
        result.reason = UNEXPECTED_ERROR.format(exc.__class__.__name__)
    return result


def diagnose_google(
    subjects: Sequence[str], fetcher: Fetcher, now: datetime, progress: Progress
) -> SourceResult:
    """Diagnostique Google Actualités : une recherche par sujet (thème ou acteur suivi),
    puis un verdict global."""
    result = SourceResult(name=GOOGLE_NEWS_NAME, type="google_news")
    for index, subject in enumerate(subjects, start=1):
        try:
            report = evaluate_google_news(subject, fetcher=fetcher, now=now)
        except Exception as exc:
            logger.exception("Recherche Google Actualités interrompue pour %s", subject)
            report = FeedReport(feed_url="", kind="google_news", subject=subject)
            report.fetch_error = UNEXPECTED_ERROR.format(exc.__class__.__name__)
            report.reason = report.fetch_error
        result.reports.append(report)
        progress(f"    {index:>2}/{len(subjects)} {subject} : {report.verdict} - {report.reason}")
    result.verdict, result.reason = google_verdict(result.reports)
    result.link_resolution = google_link_resolution(result.reports)
    return result


def google_verdict(reports: Sequence[FeedReport]) -> tuple[str, str]:
    """Verdict global de Google Actualités à partir des recherches par sujet.

    Un sujet sans article récent n'est pas un défaut de la source ; une recherche en
    erreur ou un flux incomplet en est un.
    """
    if not reports:
        return UNUSABLE, "Aucun sujet à rechercher."
    errors = [r for r in reports if r.fetch_error]
    with_articles = [r for r in reports if r.entry_count > 0]
    partial = [r for r in reports if r.verdict == PARTIAL]
    total = len(reports)
    if len(errors) == total:
        return UNUSABLE, f"Aucune recherche n'a abouti : {errors[0].fetch_error}"
    summary = f"{len(with_articles)} sujet(s) sur {total} avec des articles sur 30 jours"
    problems = []
    if errors:
        problems.append(f"{len(errors)} recherche(s) en erreur")
    if partial:
        problems.append(f"{len(partial)} flux incomplet(s)")
    if problems:
        return PARTIAL, f"{summary}, mais {', '.join(problems)}."
    if not with_articles:
        return PARTIAL, "Recherches abouties, mais aucun sujet n'a d'article sur 30 jours."
    return EXPLOITABLE, f"{summary}."


def google_link_resolution(reports: Sequence[FeedReport]) -> str:
    """« resolved » si toutes les recherches testées aboutissent chez l'éditeur, sinon
    « google_link_only »."""
    tested = [r.link_resolution for r in reports if r.link_checks]
    return RESOLVED if tested and all(v == RESOLVED for v in tested) else GOOGLE_LINK_ONLY


def run_diagnosis(
    sites: Sequence[CandidateSite],
    subjects: Sequence[str],
    fetcher: Fetcher,
    now: datetime,
    progress: Progress = print,
) -> Diagnosis:
    """Diagnostique les sites puis, si des sujets sont donnés, Google Actualités."""
    diagnosis = Diagnosis(generated_at=now, version=__version__)
    total = len(sites) + (1 if subjects else 0)
    for index, site in enumerate(sites, start=1):
        progress(f"[{index}/{total}] {site.name} ({site.site_url})...")
        result = diagnose_site(site, fetcher, now)
        progress(f"    → {result.verdict} : {result.reason}")
        diagnosis.sources.append(result)
    if subjects:
        progress(f"[{total}/{total}] {GOOGLE_NEWS_NAME} ({len(subjects)} sujet(s))...")
        result = diagnose_google(subjects, fetcher, now, progress)
        progress(f"    → {result.verdict} : {result.reason}")
        diagnosis.sources.append(result)
    return diagnosis


# --------------------------------------------------------------------------- rapports


def _fmt_date(value: datetime | None) -> str:
    """Date au format français, ou tiret."""
    return value.strftime("%d/%m/%Y") if value else "-"


def _cell(text: object) -> str:
    """Contenu sûr pour une cellule de tableau Markdown."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _pct(value: float) -> str:
    """Pourcentage lisible."""
    return f"{round(value * 100)} %"


def render_markdown(diagnosis: Diagnosis) -> str:
    """Produit le rapport Markdown en français."""
    local_time = diagnosis.generated_at.astimezone()
    lines = [
        "# Diagnostic des sources de veille",
        "",
        f"Généré le {local_time:%d/%m/%Y à %H:%M} par News Property {diagnosis.version}.",
        "",
        "## Synthèse",
        "",
        "| Source | Type | Verdict | Flux retenu | Articles (30 j) | Raison |",
        "|---|---|---|---|---|---|",
    ]
    for source in diagnosis.sources:
        if source.type == "google_news":
            recent = sum(r.recent_count for r in source.reports)
            feed = f"recherche par sujet ({len(source.reports)})"
        else:
            best = next((r for r in source.reports if r.feed_url == source.best_feed), None)
            recent = best.recent_count if best else 0
            feed = source.best_feed or "-"
        lines.append(
            f"| {_cell(source.name)} | {source.type} | **{source.verdict}** | {_cell(feed)} "
            f"| {recent} | {_cell(source.reason)} |"
        )
    lines += [
        "",
        "Verdicts : **exploitable** = flux trouvé, au moins 80 % des articles avec titre, date "
        "et lien, au moins un article sur 30 jours, liens testés valides ; **partiel** = flux "
        "trouvé mais un critère manque (indiqué dans la raison) ; **inexploitable** = aucun "
        "flux utilisable ou erreur.",
        "",
        "## Détail par source",
    ]
    for source in diagnosis.sources:
        lines += ["", f"### {source.name}", ""]
        if source.type == "google_news":
            lines += _render_google(source)
        else:
            lines += _render_site(source)
    return "\n".join(lines) + "\n"


def _render_site(source: SourceResult) -> list[str]:
    """Section Markdown d'un site."""
    lines = [
        f"- Site : {source.site_url}",
        f"- Verdict : **{source.verdict}** - {source.reason}",
    ]
    if source.homepage_error:
        lines.append(f"- Page d'accueil : {source.homepage_error}")
    lines.append(
        "- Flux trouvés : " + (", ".join(source.feeds_found) if source.feeds_found else "aucun")
    )
    tried = [a for a in source.discovery_attempts if not a.found]
    if tried:
        lines.append("- Adresses essayées sans succès :")
        lines += [f"  - {a.url} : {a.detail}" for a in tried]
    for report in source.reports:
        lines += ["", f"#### Flux {report.feed_url}", ""]
        lines += _render_feed_figures(report)
    return lines


def _render_feed_figures(report: FeedReport) -> list[str]:
    """Chiffres, exemples et liens d'un flux."""
    if report.fetch_error:
        return [f"- Erreur : {report.fetch_error}"]
    lines = [
        f"- Verdict : **{report.verdict}** - {report.reason}",
        f"- Titre du flux : {report.feed_title or '-'}",
        f"- Articles dans le flux : {report.entry_count} ; sur 30 jours : {report.recent_count} ; "
        f"plus récent : {_fmt_date(report.latest_entry)}",
        f"- Articles avec titre : {_pct(report.share_title)} ; date : {_pct(report.share_date)} ; "
        f"lien : {_pct(report.share_link)} ; les trois : {_pct(report.share_complete)}",
    ]
    if report.sample_titles:
        lines.append("- Exemples de titres :")
        lines += [f"  - {title}" for title in report.sample_titles]
    if report.link_checks:
        lines.append("- Liens testés :")
        for check in report.link_checks:
            if check.skipped:
                outcome = "non testé (robots.txt)"
            elif check.ok:
                outcome = f"HTTP {check.status_code}"
                if check.final_url and check.final_url != check.url:
                    outcome += f" → {check.final_url}"
            else:
                outcome = check.error or f"HTTP {check.status_code}"
            lines.append(f"  - {check.url} : {outcome}")
    return lines


def _render_google(source: SourceResult) -> list[str]:
    """Section Markdown de Google Actualités."""
    resolution = {
        RESOLVED: "les liens mènent directement à l'article chez l'éditeur (resolved)",
        GOOGLE_LINK_ONLY: "les liens restent des liens news.google.com (google_link_only) ; "
        "non bloquant : ils ouvrent bien le vrai article chez le lecteur",
    }.get(source.link_resolution or "", "non déterminée")
    lines = [
        f"- Verdict : **{source.verdict}** - {source.reason}",
        f"- Modèle de requête : `{GOOGLE_NEWS_QUERY_TEMPLATE}`",
        f"- Résolution des liens : {resolution}",
        "",
        "| Sujet | Verdict | Articles (30 j) | Plus récent | Principaux éditeurs "
        "| Liens | Raison |",
        "|---|---|---|---|---|---|---|",
    ]
    for report in source.reports:
        publishers = ", ".join(f"{name} ({count})" for name, count in report.publishers[:3])
        lines.append(
            f"| {_cell(report.subject)} | {report.verdict} | {report.recent_count} "
            f"| {_fmt_date(report.latest_entry)} | {_cell(publishers or '-')} "
            f"| {report.link_resolution or '-'} | {_cell(report.reason)} |"
        )
    for report in source.reports:
        if report.sample_titles or report.fetch_error:
            lines += ["", f"#### {report.subject}", "", f"- Requête : {report.feed_url}"]
            lines += _render_feed_figures(report)
    return lines


def _json_default(value: object) -> object:
    """Sérialisation JSON des types non standards."""
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Type non sérialisable : {type(value).__name__}")


def render_json(diagnosis: Diagnosis) -> str:
    """Produit le rapport JSON (mêmes données que le Markdown)."""
    return json.dumps(
        dataclasses.asdict(diagnosis), default=_json_default, ensure_ascii=False, indent=2
    )


PROPOSAL_HEADER = """\
# Proposition de configuration des sources de veille, générée par le diagnostic
# du {date}. À VÉRIFIER puis copier vers config/sources.yaml pour la valider.
# Seules les sources au verdict « exploitable » sont activées (enabled: true).
# Aucune URL n'est inventée : url est vide (null) quand aucun flux n'a été trouvé.
# Format documenté dans config/sources.example.yaml.

"""


def build_proposal(diagnosis: Diagnosis) -> dict[str, Any]:
    """Construit la proposition de configuration (format de config/sources.example.yaml)."""
    proposal: dict[str, Any] = {"sources": []}
    for source in diagnosis.sources:
        if source.type == "google_news":
            proposal["google_news"] = {
                "enabled": source.verdict == EXPLOITABLE,
                "query_template": GOOGLE_NEWS_QUERY_TEMPLATE,
                "link_resolution": source.link_resolution or GOOGLE_LINK_ONLY,
                "verdict": source.verdict,
                "note": source.reason,
                "subjects": [r.subject for r in source.reports],
            }
            continue
        proposal["sources"].append(
            {
                "name": source.name,
                "type": "rss",
                "url": source.best_feed,
                "enabled": source.verdict == EXPLOITABLE and source.best_feed is not None,
                "verdict": source.verdict,
                "note": source.reason,
            }
        )
    return proposal


def render_proposal(diagnosis: Diagnosis) -> str:
    """Produit le texte YAML de sources.proposed.yaml."""
    header = PROPOSAL_HEADER.format(date=f"{diagnosis.generated_at.astimezone():%d/%m/%Y %H:%M}")
    body = yaml.safe_dump(build_proposal(diagnosis), allow_unicode=True, sort_keys=False, width=100)
    return header + body


def write_reports(diagnosis: Diagnosis, reports_dir: Path) -> dict[str, Path]:
    """Écrit le rapport .md, le .json et sources.proposed.yaml ; renvoie leurs chemins."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{diagnosis.generated_at.astimezone():%Y%m%d-%H%M}"
    paths = {
        "markdown": reports_dir / f"sources-{stamp}.md",
        "json": reports_dir / f"sources-{stamp}.json",
        "proposal": reports_dir / "sources.proposed.yaml",
    }
    paths["markdown"].write_text(render_markdown(diagnosis), encoding="utf-8")
    paths["json"].write_text(render_json(diagnosis), encoding="utf-8")
    paths["proposal"].write_text(render_proposal(diagnosis), encoding="utf-8")
    return paths


# --------------------------------------------------------------------------- ligne de commande


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        prog="python -m news_property.tools.diagnose_sources",
        description="Teste les sources de veille candidates et propose une configuration.",
    )
    parser.add_argument(
        "--only",
        metavar="NOM",
        help="ne tester qu'une source (ex. « Business Immo », « Google »), un acteur ou un thème",
    )
    parser.add_argument("--no-google", action="store_true", help="ne pas tester Google Actualités")
    parser.add_argument("--open", action="store_true", help="ouvrir le rapport à la fin")
    parser.add_argument(
        "--install-proposal",
        action="store_true",
        help="sans nouveau diagnostic : copier la dernière proposition (sources.proposed.yaml) "
        "vers config/sources.yaml, après vérification",
    )
    return parser.parse_args(argv)


def install_proposal(reports_dir: Path, target: Path = SOURCES_FILE) -> Path | None:
    """Copie sources.proposed.yaml (dernier diagnostic) vers config/sources.yaml.

    La proposition est d'abord relue par load_sources : rien n'est copié si elle est
    invalide (SourcesConfigError). Un sources.yaml déjà présent est gardé en
    sources.yaml.bak. Renvoie le chemin de la sauvegarde, ou None s'il n'y en avait pas.
    Lève FileNotFoundError si aucune proposition n'existe.
    """
    proposal = reports_dir / "sources.proposed.yaml"
    if not proposal.is_file():
        raise FileNotFoundError(
            f"Aucune proposition dans {reports_dir} : lancez d'abord le diagnostic."
        )
    load_sources(proposal)  # SourcesConfigError si le fichier est invalide
    backup = None
    if target.is_file():
        backup = target.with_suffix(".yaml.bak")
        shutil.copyfile(target, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(proposal, target)
    return backup


def select_targets(
    only: str | None,
    no_google: bool,
    sites: Sequence[CandidateSite],
    subjects: Sequence[str],
) -> tuple[list[CandidateSite], list[str]]:
    """Choisit les sites et sujets Google Actualités à tester selon --only et --no-google.

    Lève ValueError si --only ne correspond à aucune source ni sujet.
    """
    selected_subjects = [] if no_google else list(subjects)
    if not only:
        return list(sites), selected_subjects
    wanted = only.strip().casefold()
    matching_sites = [s for s in sites if s.name.casefold() == wanted]
    if matching_sites:
        return matching_sites, []
    if wanted in (GOOGLE_NEWS_NAME.casefold(), "google", "google news"):
        return [], list(subjects)
    matching_subjects = [s for s in subjects if s.casefold() == wanted]
    if matching_subjects:
        return [], matching_subjects
    names = ", ".join([s.name for s in sites] + [GOOGLE_NEWS_NAME] + list(subjects))
    raise ValueError(f"Source ou sujet inconnu : « {only} ». Choix possibles : {names}.")


def open_file(path: Path) -> None:
    """Ouvre un fichier avec l'application par défaut (Bloc-notes en repli sous Windows)."""
    try:
        if sys.platform == "win32":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except OSError:
                subprocess.Popen(["notepad.exe", str(path)])
        else:
            webbrowser.open(path.as_uri())
    except Exception:
        logger.warning("Impossible d'ouvrir %s", path, exc_info=True)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    sites: Sequence[CandidateSite] = CANDIDATE_SITES,
    subjects: Sequence[str] | None = None,
    progress: Progress = print,
) -> int:
    """Lance le diagnostic et écrit les rapports. Renvoie le code de sortie.

    subjects : sujets Google Actualités ; par défaut, les thèmes puis les acteurs de
    config/acteurs.yaml. 0 même si des sources sont inexploitables ; 1 si l'outil
    lui-même plante ; 2 en cas de configuration ou d'argument invalide.
    """
    args = parse_args(argv)
    try:
        settings = settings or load_settings()
        if subjects is None:
            subjects = google_news_subjects()
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    configure_logging(settings, console=False)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.install_proposal:
        try:
            backup = install_proposal(settings.reports_dir)
        except (FileNotFoundError, SourcesConfigError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        progress(f"Liste des sources installée : {SOURCES_FILE}")
        if backup is not None:
            progress(f"L'ancienne liste a été gardée en {backup.name}.")
        progress("Vous pouvez lancer la collecte (collecter_veille.bat ou bouton Collecter).")
        return 0

    try:
        targets = select_targets(args.only, args.no_google, sites, subjects)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    try:
        progress(f"Diagnostic des sources de veille (News Property {__version__})")
        diagnosis = run_diagnosis(*targets, fetcher, now or datetime.now(UTC), progress)
        paths = write_reports(diagnosis, settings.reports_dir)
    except Exception:
        logger.exception("Le diagnostic des sources a échoué")
        print(
            "L'outil de diagnostic a rencontré une erreur inattendue. "
            f"Détail dans le journal : {settings.logs_dir}",
            file=sys.stderr,
        )
        return 1
    finally:
        if own_fetcher:
            fetcher.close()

    progress("")
    progress(f"Rapport : {paths['markdown']}")
    progress(f"Données : {paths['json']}")
    progress(f"Proposition de configuration : {paths['proposal']}")
    if args.open:
        open_file(paths["markdown"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
