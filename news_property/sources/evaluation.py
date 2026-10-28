"""Évaluation de la qualité d'un flux RSS/Atom et des recherches Google Actualités."""

from __future__ import annotations

import calendar
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

import feedparser

from news_property.http_client import Fetcher, get_default_fetcher
from news_property.sources.candidates import (
    GOOGLE_NEWS_PARAMS,
    GOOGLE_NEWS_QUERY_TEMPLATE,
    GOOGLE_NEWS_SEARCH_URL,
)

logger = logging.getLogger(__name__)

EXPLOITABLE = "exploitable"
PARTIAL = "partiel"
UNUSABLE = "inexploitable"
VERDICT_RANK = {EXPLOITABLE: 2, PARTIAL: 1, UNUSABLE: 0}

RESOLVED = "resolved"
GOOGLE_LINK_ONLY = "google_link_only"

RECENT_DAYS = 30
COMPLETENESS_THRESHOLD = 0.8
SAMPLE_SIZE = 3
LINKS_TO_CHECK = 3


@dataclass
class LinkCheck:
    """Vérification d'un lien d'article."""

    url: str
    final_url: str | None = None
    status_code: int | None = None
    error: str | None = None
    skipped: bool = False  # True si non testé (robots.txt)

    @property
    def ok(self) -> bool:
        """Vrai si le lien répond en 2xx."""
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 300


@dataclass
class FeedReport:
    """Bilan d'un flux : chiffres, exemples, liens testés et verdict."""

    feed_url: str
    kind: str = "rss"  # "rss" ou "google_news"
    http_status: int | None = None
    fetch_error: str | None = None
    feed_title: str | None = None
    entry_count: int = 0
    latest_entry: datetime | None = None
    recent_count: int = 0
    share_title: float = 0.0
    share_date: float = 0.0
    share_link: float = 0.0
    share_complete: float = 0.0
    sample_titles: list[str] = field(default_factory=list)
    link_checks: list[LinkCheck] = field(default_factory=list)
    verdict: str = UNUSABLE
    reason: str = ""
    # Spécifique à Google Actualités
    subject: str | None = None  # acteur ou thème recherché
    query: str | None = None
    link_resolution: str | None = None
    publishers: list[tuple[str, int]] = field(default_factory=list)


def _entry_date(entry: Any) -> datetime | None:
    """Date de publication (ou de mise à jour) d'une entrée, en UTC."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)


def _share(count: int, total: int) -> float:
    """Proportion arrondie à 3 décimales."""
    return round(count / total, 3) if total else 0.0


def _pct(value: float) -> str:
    """Pourcentage lisible (ex. « 75 % »)."""
    return f"{round(value * 100)} %"


def build_google_news_url(subject: str, query_template: str = GOOGLE_NEWS_QUERY_TEMPLATE) -> str:
    """Construit l'URL du flux RSS de recherche Google Actualités pour un sujet (acteur
    suivi ou thème).

    La requête (sujet entre guillemets + when:30d) est entièrement encodée :
    guillemets → %22, apostrophes → %27, espaces → %20.
    """
    query = query_template.format(subject=subject)
    return f"{GOOGLE_NEWS_SEARCH_URL}?q={quote(query, safe='')}&{GOOGLE_NEWS_PARAMS}"


def _is_google_host(url: str | None) -> bool:
    """Vrai si l'URL pointe vers un domaine Google (redirection non résolue, consentement...)."""
    host = (urlsplit(url or "").hostname or "").lower()
    return ".google." in f".{host}"


def evaluate_feed(
    feed_url: str,
    *,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
) -> FeedReport:
    """Évalue un flux RSS/Atom : chiffres, exemples, test de 3 liens et verdict.

    now : date de référence (injectée dans les tests) ; par défaut, maintenant (UTC).
    """
    return _evaluate(FeedReport(feed_url=feed_url), fetcher or get_default_fetcher(), now)


def evaluate_google_news(
    subject: str,
    *,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
    query_template: str = GOOGLE_NEWS_QUERY_TEMPLATE,
) -> FeedReport:
    """Évalue la recherche Google Actualités d'un sujet (acteur suivi ou thème).

    En plus de evaluate_feed : relève l'éditeur (balise <source>) de chaque entrée et
    vérifie si les liens news.google.com mènent, par simple redirection HTTP, à l'URL
    d'origine de l'éditeur (link_resolution = « resolved ») ou restent chez Google
    (« google_link_only », non bloquant : le lien ouvre le vrai article chez le lecteur).
    """
    report = FeedReport(
        feed_url=build_google_news_url(subject, query_template),
        kind="google_news",
        subject=subject,
        query=query_template.format(subject=subject),
    )
    return _evaluate(report, fetcher or get_default_fetcher(), now)


def _evaluate(report: FeedReport, fetcher: Fetcher, now: datetime | None) -> FeedReport:
    """Remplit le rapport : téléchargement, analyse, liens, verdict."""
    now = now or datetime.now(UTC)
    result = fetcher.fetch(report.feed_url)
    report.http_status = result.status_code
    if not result.ok:
        report.fetch_error = result.error or "Réponse invalide."
        report.verdict, report.reason = UNUSABLE, report.fetch_error
        return report

    parsed = feedparser.parse(result.content)
    if not parsed.get("version"):
        report.verdict = UNUSABLE
        report.reason = "Le contenu reçu n'est pas un flux RSS/Atom lisible."
        return report

    report.feed_title = (parsed.feed.get("title") or "").strip() or None
    entries = parsed.entries
    report.entry_count = total = len(entries)
    dates = [_entry_date(e) for e in entries]
    has_title = [bool((e.get("title") or "").strip()) for e in entries]
    has_link = [bool((e.get("link") or "").strip()) for e in entries]
    report.share_title = _share(sum(has_title), total)
    report.share_date = _share(sum(d is not None for d in dates), total)
    report.share_link = _share(sum(has_link), total)
    report.share_complete = _share(
        sum(
            title and link and day is not None
            for title, link, day in zip(has_title, has_link, dates, strict=True)
        ),
        total,
    )
    known_dates = [d for d in dates if d is not None]
    report.latest_entry = max(known_dates) if known_dates else None
    report.recent_count = sum(now - timedelta(days=RECENT_DAYS) <= d for d in known_dates)
    report.sample_titles = [
        e.get("title", "").strip() for e in entries if (e.get("title") or "").strip()
    ][:SAMPLE_SIZE]

    if report.kind == "google_news":
        publishers = Counter(
            (e.get("source") or {}).get("title", "").strip()
            for e in entries
            if (e.get("source") or {}).get("title")
        )
        report.publishers = publishers.most_common()

    links = [e.get("link").strip() for e in entries if (e.get("link") or "").strip()]
    for link in links[:LINKS_TO_CHECK]:
        report.link_checks.append(_check_link(link, report.kind, fetcher))
    if report.kind == "google_news":
        resolved = any(c.ok and not _is_google_host(c.final_url) for c in report.link_checks)
        report.link_resolution = RESOLVED if resolved else GOOGLE_LINK_ONLY

    report.verdict, report.reason = compute_verdict(report)
    return report


def _check_link(url: str, kind: str, fetcher: Fetcher) -> LinkCheck:
    """Teste un lien d'article (statut HTTP final après redirections)."""
    # Les articles des sites sont des pages HTML visitées : robots.txt s'applique.
    # Les liens Google Actualités sont des redirections fournies par le flux lui-même.
    result = fetcher.fetch(url, check_robots=(kind != "google_news"))
    return LinkCheck(
        url=url,
        final_url=result.final_url,
        status_code=result.status_code,
        error=result.error,
        skipped=result.blocked_by_robots,
    )


def compute_verdict(report: FeedReport) -> tuple[str, str]:
    """Calcule le verdict d'un flux téléchargé et analysé, avec sa raison en français.

    exploitable : ≥ 80 % des entrées avec titre + date + lien, au moins une entrée
    sur 30 jours, liens testés en 2xx. partiel : un critère manque (précisé).
    inexploitable : flux vide ou illisible.
    """
    if report.fetch_error:
        return UNUSABLE, report.fetch_error
    if report.entry_count == 0:
        if report.kind == "google_news":
            return UNUSABLE, "Aucun article trouvé sur 30 jours pour cette recherche."
        return UNUSABLE, "Le flux est vide (aucun article)."

    problems: list[str] = []
    if report.share_complete < COMPLETENESS_THRESHOLD:
        missing = [
            f"{label} ({_pct(share)} des articles en ont)"
            for label, share in (
                ("titre", report.share_title),
                ("date", report.share_date),
                ("lien", report.share_link),
            )
            if share < COMPLETENESS_THRESHOLD
        ]
        if missing:
            problems.append("informations manquantes : " + ", ".join(missing))
        else:
            problems.append(
                f"seulement {_pct(report.share_complete)} des articles ont à la fois "
                "un titre, une date et un lien"
            )
    if report.recent_count == 0:
        if report.latest_entry is None:
            problems.append("aucune date de publication, impossible de vérifier la fraîcheur")
        else:
            problems.append(
                f"aucun article depuis {RECENT_DAYS} jours "
                f"(le plus récent date du {report.latest_entry:%d/%m/%Y})"
            )
    tested = [c for c in report.link_checks if not c.skipped]
    failed = [c for c in tested if not c.ok]
    if failed:
        details = "; ".join(c.error or f"HTTP {c.status_code}" for c in failed)
        problems.append(f"{len(failed)} lien(s) testé(s) sur {len(tested)} en erreur ({details})")

    if problems:
        return PARTIAL, "Flux trouvé, mais " + " ; ".join(problems) + "."
    reason = f"Flux complet et à jour : {report.recent_count} article(s) sur {RECENT_DAYS} jours."
    if report.link_checks and not tested:
        reason += " Liens non testés (robots.txt)."
    return EXPLOITABLE, reason
