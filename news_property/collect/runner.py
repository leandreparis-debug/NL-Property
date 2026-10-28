"""Orchestrateur de la collecte de veille d'un mois."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from news_property.collect.config import (
    RULES_FILE,
    SOURCES_FILE,
    CollectRules,
    SourcesConfig,
    load_rules,
    load_sources,
)
from news_property.collect.excerpt import PageContent, choose_excerpt, extract_page
from news_property.collect.feeds import (
    FeedEntry,
    FeedError,
    fetch_feed,
    parse_google_entry,
    parse_rss_entry,
)
from news_property.collect.matching import ActorMatcher, TermMatcher, classify
from news_property.collect.periods import in_month
from news_property.collect.text import title_key
from news_property.collect.urls import clean_url, url_key
from news_property.config import Settings
from news_property.db.connection import transaction
from news_property.detect.rules import DETECTION_RULES_FILE, DetectionRulesError
from news_property.detect.runner import run_detection
from news_property.http_client import Fetcher, get_default_fetcher
from news_property.models import Actor, Article, validate_month, validate_url
from news_property.repositories import actors, articles, collection_runs, newsletters
from news_property.sources.candidates import GOOGLE_NEWS_NAME
from news_property.sources.evaluation import build_google_news_url

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

TITLE_SIMILARITY = 0.9
STALE_RUN_AFTER = timedelta(hours=3)
RUBRICS = (1, 2)


def system_clock() -> datetime:
    """Horloge réelle (UTC). Les tests injectent la leur."""
    return datetime.now(UTC)


class CollectionAlreadyRunning(RuntimeError):
    """Une collecte est déjà en cours (message en français)."""


@dataclass
class SourceStat:
    """Bilan d'une source configurée."""

    name: str
    kind: str  # "rss" ou "google_news"
    entries: int = 0
    added: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Progress:
    """Avancement transmis pendant la collecte."""

    source: str
    index: int
    total: int
    entries_read: int
    added: int


@dataclass
class CollectionReport:
    """Bilan d'une collecte."""

    month: str
    newsletter_id: int
    run_id: int
    started_at: datetime
    status: str = "running"
    finished_at: datetime | None = None
    entries_read: int = 0
    added_by_rubric: dict[int, int] = field(default_factory=lambda: dict.fromkeys(RUBRICS, 0))
    duplicates: int = 0
    out_of_period: int = 0
    without_date: int = 0
    off_topic: int = 0
    sources: list[SourceStat] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    added_articles: list[Article] = field(default_factory=list)
    detection: dict[str, Any] | None = None  # bilan de la détection lancée après la collecte
    detection_error: str | None = None

    @property
    def added_total(self) -> int:
        """Nombre total d'articles ajoutés."""
        return sum(self.added_by_rubric.values())

    def added_by_source_name(self) -> dict[str, int]:
        """Articles ajoutés par éditeur (source_name), du plus fréquent au moins fréquent."""
        return dict(Counter(a.source_name for a in self.added_articles).most_common())

    def to_stats(self) -> dict[str, Any]:
        """Statistiques enregistrées dans collection_runs.stats (JSON)."""
        return {
            "month": self.month,
            "entries_read": self.entries_read,
            "added_rubric_1": self.added_by_rubric[1],
            "added_rubric_2": self.added_by_rubric[2],
            "duplicates": self.duplicates,
            "out_of_period": self.out_of_period,
            "without_date": self.without_date,
            "off_topic": self.off_topic,
            "sources": [
                {"name": s.name, "kind": s.kind, "entries": s.entries, "added": s.added}
                for s in self.sources
            ],
            "added_by_source_name": self.added_by_source_name(),
            "detection": self.detection,
            "detection_error": self.detection_error,
        }


@dataclass
class _Seen:
    """Articles déjà présents (en base ou ajoutés pendant la collecte) pour le dédoublonnage."""

    url_keys: set[str] = field(default_factory=set)
    titles: list[tuple[int | None, str]] = field(default_factory=list)

    def add(self, url: str, actor_id: int | None, title: str) -> None:
        """Mémorise un article."""
        self.url_keys.add(url_key(url))
        self.titles.append((actor_id, title_key(title)))

    def has_url(self, url: str) -> bool:
        """URL déjà vue (après normalisation) ?"""
        return url_key(url) in self.url_keys

    def has_similar_title(self, actor_id: int | None, title: str) -> bool:
        """Titre très proche (≥ 0,9) d'un article du même acteur déjà vu ?"""
        key = title_key(title)
        for other_actor, other in self.titles:
            if other_actor != actor_id:
                continue
            matcher = SequenceMatcher(None, key, other)
            if (
                matcher.real_quick_ratio() >= TITLE_SIMILARITY
                and matcher.ratio() >= TITLE_SIMILARITY
            ):
                return True
        return False


@dataclass
class _Context:
    """État partagé pendant une collecte."""

    conn: sqlite3.Connection
    fetcher: Fetcher
    clock: Clock
    month: str
    newsletter_id: int
    report: CollectionReport
    actor_matcher: ActorMatcher
    topic_terms: TermMatcher
    exclusions: TermMatcher
    seen: _Seen
    link_resolution: str = "google_link_only"
    failed_feeds: int = 0


def _is_google(url: str | None) -> bool:
    """URL d'un domaine Google (lien de redirection news.google.com, page de consentement)."""
    host = (urlsplit(url or "").hostname or "").lower()
    return ".google." in f".{host}"


def _fetch_page(ctx: _Context, url: str) -> PageContent | None:
    """Télécharge la page d'un article (robots.txt respecté) et en extrait le contenu."""
    result = ctx.fetcher.fetch(url, check_robots=True)
    if not result.ok or not result.text:
        logger.info("Page non récupérée (%s) : %s", url, result.error)
        return None
    return extract_page(result.text)


def _resolve_google(ctx: _Context, link: str) -> tuple[str, PageContent | None]:
    """Suit la redirection d'un lien Google Actualités.

    Renvoie (URL de l'éditeur, contenu de la page) si la redirection aboutit chez
    l'éditeur, sinon (lien Google tel quel, None) : il ouvre bien le vrai article.
    """
    result = ctx.fetcher.fetch(link)
    final = result.final_url
    if not result.ok or not final or _is_google(final):
        return link, None
    try:
        final = validate_url(final)
    except ValueError:
        return link, None
    page = None
    if ctx.fetcher.allowed_by_robots(final) and result.text:
        page = extract_page(result.text)
    return final, page


def _process_entry(ctx: _Context, entry: FeedEntry, is_google: bool, stat: SourceStat) -> None:
    """Applique période, classement, dédoublonnage, extraction, puis enregistre l'article."""
    report = ctx.report
    if entry.published is None:
        report.without_date += 1
        return
    if not in_month(entry.published, ctx.month):
        report.out_of_period += 1
        return
    classification = classify(
        entry.title, entry.summary, ctx.actor_matcher, ctx.topic_terms, ctx.exclusions
    )
    if classification is None:
        report.off_topic += 1
        return
    try:
        url = clean_url(validate_url(entry.link))
    except ValueError:
        report.off_topic += 1  # lien absent ou non http(s) : jamais stocké
        logger.info("Entrée ignorée, lien invalide : %r", entry.link)
        return
    actor: Actor | None = classification.actor
    actor_id = actor.id if actor else None
    if ctx.seen.has_url(url) or ctx.seen.has_similar_title(actor_id, entry.title):
        report.duplicates += 1
        return

    page: PageContent | None = None
    if is_google:
        if ctx.link_resolution == "resolved":
            url, page = _resolve_google(ctx, url)
            url = clean_url(url)
            if ctx.seen.has_url(url):
                report.duplicates += 1
                return
    else:
        page = _fetch_page(ctx, url)

    article = Article(
        newsletter_id=ctx.newsletter_id,
        rubric=classification.rubric,
        title=entry.title,
        source_name=entry.source_name,
        url=url,
        actor_id=actor_id,
        published_at=entry.published,
        excerpt=choose_excerpt(page, entry.summary) or None,
        body_text=(page.body_text or None) if page else None,
        selected=True,
        collected_at=ctx.clock(),
    )
    added = articles.add(ctx.conn, article)
    ctx.seen.add(url, actor_id, entry.title)
    if added is None:
        report.duplicates += 1
        return
    report.added_by_rubric[added.rubric] += 1
    report.added_articles.append(added)
    stat.added += 1


def _collect_feed(ctx: _Context, stat: SourceStat, url: str, label: str, is_google: bool) -> None:
    """Lit un flux et traite ses entrées ; une erreur est consignée sans tout arrêter."""
    try:
        raw_entries = fetch_feed(ctx.fetcher, url)
    except FeedError as exc:
        ctx.failed_feeds += 1
        message = f"{label} : {exc}"
        stat.errors.append(message)
        ctx.report.errors.append(message)
        return
    except Exception as exc:
        ctx.failed_feeds += 1
        logger.exception("Lecture du flux %s interrompue", url)
        message = f"{label} : erreur inattendue ({exc.__class__.__name__}), détail dans le journal."
        stat.errors.append(message)
        ctx.report.errors.append(message)
        return
    for raw in raw_entries:
        entry = parse_google_entry(raw) if is_google else parse_rss_entry(raw, stat.name)
        if entry is None:
            continue
        stat.entries += 1
        ctx.report.entries_read += 1
        try:
            _process_entry(ctx, entry, is_google, stat)
        except Exception as exc:
            logger.exception("Entrée non traitée : %s", entry.link)
            message = (
                f"{label} : un article n'a pas pu être traité ({exc.__class__.__name__}), "
                "détail dans le journal."
            )
            stat.errors.append(message)
            ctx.report.errors.append(message)


def _detect_after_collection(
    conn: sqlite3.Connection, month: str, rules_path: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """Lance la détection (transactions, surfaces) ; renvoie (bilan, erreur)."""
    try:
        detection = run_detection(conn, month, rules_path=rules_path)
    except DetectionRulesError as exc:
        return None, f"Détection non lancée : {exc}"
    except Exception as exc:
        logger.exception("Détection du mois %s interrompue", month)
        return None, (
            f"Détection interrompue par une erreur inattendue ({exc.__class__.__name__}), "
            "détail dans le journal."
        )
    return detection.summary(), None


def _start_run(conn: sqlite3.Connection, newsletter_id: int, now: datetime) -> int:
    """Verrou : refuse si une collecte est en cours, sinon enregistre le début de celle-ci.

    Une collecte « running » plus vieille que STALE_RUN_AFTER est considérée comme
    interrompue (arrêt brutal de l'application) et passe en « failed ».
    """
    with transaction(conn):
        for run in collection_runs.list_running(conn):
            if now - run.started_at < STALE_RUN_AFTER:
                raise CollectionAlreadyRunning(
                    "Une collecte est déjà en cours (lancée le "
                    f"{run.started_at.astimezone():%d/%m/%Y à %H:%M}). "
                    "Attendez qu'elle se termine avant d'en lancer une autre."
                )
            assert run.id is not None
            collection_runs.finish(
                conn, run.id, "failed", run.stats, [*run.errors, "Collecte interrompue."], now
            )
        run = collection_runs.start(conn, newsletter_id, now)
    assert run.id is not None
    return run.id


def run_collection(
    conn: sqlite3.Connection,
    settings: Settings,
    month: str,
    clock: Clock = system_clock,
    *,
    fetcher: Fetcher | None = None,
    sources_path: Path = SOURCES_FILE,
    rules_path: Path = RULES_FILE,
    detection_rules_path: Path = DETECTION_RULES_FILE,
    on_progress: Callable[[Progress], None] | None = None,
) -> CollectionReport:
    """Collecte les articles du mois (AAAA-MM) depuis les sources activées.

    Ajoute uniquement les nouveaux articles (selected = 1, en fin de rubrique) : les
    articles déjà présents ne sont jamais modifiés ni supprimés. Une source en erreur
    n'interrompt pas la collecte (statut « partial »). La détection des transactions et
    des surfaces (étape 5) est lancée à la fin ; si elle échoue (règles invalides), la
    collecte reste valable et l'erreur figure dans report.detection_error.

    settings est réservé aux évolutions (dossiers de données) ; la configuration vient de
    sources_path et rules_path.

    Lève ValueError (mois invalide), SourcesConfigError / RulesConfigError (configuration :
    aucune collecte n'est alors lancée) ou CollectionAlreadyRunning (verrou).
    """
    month = validate_month(month)
    sources: SourcesConfig = load_sources(sources_path)
    rules: CollectRules = load_rules(rules_path)
    fetcher = fetcher or get_default_fetcher()

    newsletter = newsletters.get_or_create(conn, month)
    assert newsletter.id is not None
    started_at = clock()
    run_id = _start_run(conn, newsletter.id, started_at)
    report = CollectionReport(
        month=month, newsletter_id=newsletter.id, run_id=run_id, started_at=started_at
    )
    try:
        tracked = actors.list_all(conn, tracked_only=True)
        seen = _Seen()
        for rubric in RUBRICS:
            for existing in articles.list_for_rubric(conn, newsletter.id, rubric):
                seen.add(existing.url, existing.actor_id, existing.title)
        ctx = _Context(
            conn=conn,
            fetcher=fetcher,
            clock=clock,
            month=month,
            newsletter_id=newsletter.id,
            report=report,
            actor_matcher=ActorMatcher(tracked),
            topic_terms=TermMatcher(rules.topic_terms),
            exclusions=TermMatcher(rules.exclusions),
            seen=seen,
            link_resolution=sources.google_news.link_resolution if sources.google_news else "",
        )

        steps: list[tuple[SourceStat, str, str, bool]] = []
        for source in sources.rss:
            stat = SourceStat(name=source.name, kind="rss")
            report.sources.append(stat)
            steps.append((stat, source.url, source.name, False))
        if sources.google_news is not None:
            stat = SourceStat(name=GOOGLE_NEWS_NAME, kind="google_news")
            report.sources.append(stat)
            # Un flux par thème, puis un par acteur suivi.
            subjects = [*sources.google_news.topics, *(actor.name for actor in tracked)]
            for subject in subjects:
                url = build_google_news_url(subject, sources.google_news.query_template)
                steps.append((stat, url, f"{GOOGLE_NEWS_NAME} ({subject})", True))

        for index, (stat, url, label, is_google) in enumerate(steps, start=1):
            if on_progress:
                on_progress(
                    Progress(label, index, len(steps), report.entries_read, report.added_total)
                )
            _collect_feed(ctx, stat, url, label, is_google)

        report.detection, report.detection_error = _detect_after_collection(
            conn, month, detection_rules_path
        )
        if not report.errors:
            report.status = "success"
        elif ctx.failed_feeds >= len(steps):
            report.status = "failed"  # aucune source n'a pu être lue
        else:
            report.status = "partial"
    except Exception as exc:
        logger.exception("Collecte du mois %s interrompue", month)
        report.status = "failed"
        report.errors.append(
            f"La collecte s'est arrêtée sur une erreur inattendue ({exc.__class__.__name__}), "
            "détail dans le journal."
        )
        report.finished_at = clock()
        collection_runs.finish(
            conn, run_id, "failed", report.to_stats(), report.errors, report.finished_at
        )
        raise
    report.finished_at = clock()
    collection_runs.finish(
        conn, run_id, report.status, report.to_stats(), report.errors, report.finished_at
    )
    if on_progress:
        on_progress(Progress("", len(steps), len(steps), report.entries_read, report.added_total))
    return report
