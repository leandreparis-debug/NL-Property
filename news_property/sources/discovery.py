"""Découverte des flux RSS/Atom d'un site."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from news_property.http_client import Fetcher, FetchResult, get_default_fetcher

logger = logging.getLogger(__name__)

FEED_TYPES = ("application/rss+xml", "application/atom+xml")
COMMON_FEED_PATHS = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/atom.xml",
    "/actualites/feed",
    "/actualite/rss",
)


@dataclass
class FeedAttempt:
    """Une URL testée lors de la découverte, et ce qu'on en a conclu."""

    url: str
    origin: str  # "declared" (balise <link>) ou "guessed" (chemin courant)
    found: bool
    detail: str


@dataclass
class DiscoveryResult:
    """Résultat détaillé de la découverte des flux d'un site."""

    site_url: str
    feeds: list[str] = field(default_factory=list)
    homepage_error: str | None = None
    declared: list[str] = field(default_factory=list)
    attempts: list[FeedAttempt] = field(default_factory=list)


def is_parsable_feed(content: bytes) -> bool:
    """Indique si le contenu est un flux RSS ou Atom reconnu par feedparser."""
    if not content:
        return False
    parsed = feedparser.parse(content)
    return bool(parsed.get("version"))


def extract_declared_feeds(html: str, base_url: str) -> list[str]:
    """Renvoie les URLs absolues des balises <link rel="alternate"> de type RSS/Atom.

    Ne lève pas d'exception sur un HTML malformé (renvoie ce qui a pu être lu).
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # HTML trop abîmé pour l'analyseur : on n'en tire rien
        logger.warning("HTML illisible pour %s", base_url, exc_info=True)
        return []
    urls: list[str] = []
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rels = [rel] if isinstance(rel, str) else rel
        link_type = (link.get("type") or "").strip().lower()
        href = (link.get("href") or "").strip()
        if href and "alternate" in [r.lower() for r in rels] and link_type in FEED_TYPES:
            absolute = urljoin(base_url, href)
            if absolute not in urls:
                urls.append(absolute)
    return urls


def _origin(url: str) -> str:
    """Schéma et domaine d'une URL (ex. https://www.exemple.fr)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _normalize(url: str) -> str:
    """Forme normalisée d'une URL pour la détection des doublons."""
    return url.rstrip("/").lower()


def discover_feeds_detailed(site_url: str, fetcher: Fetcher | None = None) -> DiscoveryResult:
    """Cherche les flux d'un site et renvoie le détail des essais.

    1. lit la page d'accueil (en respectant robots.txt) et relève les flux déclarés ;
    2. essaie les chemins courants (/feed, /rss, ...) ;
    3. ne garde que les URLs qui répondent avec un flux lisible, sans doublon.

    Si le site est injoignable (aucune réponse HTTP), la découverte s'arrête là.
    """
    fetcher = fetcher or get_default_fetcher()
    result = DiscoveryResult(site_url=site_url)

    home = fetcher.fetch(site_url, check_robots=True)
    base_origins = [_origin(site_url)]
    if home.ok:
        base_url = home.final_url or site_url
        result.declared = extract_declared_feeds(home.text, base_url)
        if _origin(base_url) not in base_origins:
            base_origins.insert(0, _origin(base_url))
    else:
        result.homepage_error = home.error
        if home.status_code is None and not home.blocked_by_robots:
            # Site injoignable (réseau, délai dépassé...) : inutile d'essayer d'autres chemins.
            return result

    candidates: list[tuple[str, str, bool]] = [(url, "declared", False) for url in result.declared]
    for origin in base_origins:
        # Les chemins devinés ne sont pas déclarés par le site : robots.txt s'applique.
        candidates += [(origin + path, "guessed", True) for path in COMMON_FEED_PATHS]

    seen_urls: set[str] = set()
    seen_contents: set[str] = set()
    for url, origin, check_robots in candidates:
        if _normalize(url) in seen_urls:
            continue
        seen_urls.add(_normalize(url))
        response = fetcher.fetch(url, check_robots=check_robots)
        found, detail = _check_candidate(response, seen_urls, seen_contents, result.feeds)
        if found:
            result.feeds.append(response.final_url or url)
        result.attempts.append(FeedAttempt(url=url, origin=origin, found=found, detail=detail))
    return result


def _check_candidate(
    response: FetchResult, seen_urls: set[str], seen_contents: set[str], feeds: list[str]
) -> tuple[bool, str]:
    """Décide si une réponse est un nouveau flux valide ; renvoie (trouvé, explication)."""
    if not response.ok:
        return False, response.error or "Réponse invalide."
    final = response.final_url or response.url
    if any(_normalize(final) == _normalize(feed) for feed in feeds):
        return False, "Doublon d'un flux déjà trouvé (redirection)."
    if not is_parsable_feed(response.content):
        return False, "La réponse n'est pas un flux RSS/Atom."
    digest = hashlib.sha256(response.content).hexdigest()
    if digest in seen_contents:
        return False, "Doublon d'un flux déjà trouvé (même contenu)."
    seen_contents.add(digest)
    seen_urls.add(_normalize(final))
    return True, "Flux RSS/Atom valide."


def discover_feeds(site_url: str, fetcher: Fetcher | None = None) -> list[str]:
    """Renvoie les URLs des flux RSS/Atom exploitables d'un site, sans doublon."""
    return discover_feeds_detailed(site_url, fetcher).feeds
