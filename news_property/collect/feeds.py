"""Lecture des flux RSS/Atom (sites et Google Actualités) en entrées normalisées."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser

from news_property.collect.text import fold, html_to_text
from news_property.http_client import Fetcher


class FeedError(Exception):
    """Flux illisible ou inaccessible ; le message (en français) va dans le bilan."""


@dataclass(frozen=True)
class FeedEntry:
    """Entrée de flux en texte brut.

    published est None si le flux ne donne pas de date exploitable. link est l'URL
    fournie par le flux (jamais construite ni devinée).
    """

    title: str
    link: str
    published: datetime | None
    summary: str
    source_name: str


def _entry_date(entry: Any) -> datetime | None:
    """Date de publication (ou de mise à jour) de l'entrée, en UTC (feedparser normalise)."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    except OverflowError, ValueError, TypeError:
        return None


def fetch_feed(fetcher: Fetcher, url: str) -> list[Any]:
    """Télécharge et analyse un flux ; renvoie les entrées feedparser.

    Lève FeedError (message en français) si le flux est inaccessible ou illisible.
    """
    result = fetcher.fetch(url)
    if not result.ok:
        raise FeedError(result.error or "Flux inaccessible.")
    parsed = feedparser.parse(result.content)
    if not parsed.get("version"):
        raise FeedError("Le contenu reçu n'est pas un flux RSS/Atom lisible.")
    return list(parsed.entries)


def parse_rss_entry(entry: Any, source_name: str) -> FeedEntry | None:
    """Entrée d'un flux de site ; None si elle n'a ni titre ni lien."""
    title = html_to_text(entry.get("title"))
    link = (entry.get("link") or "").strip()
    if not title or not link:
        return None
    return FeedEntry(
        title=title,
        link=link,
        published=_entry_date(entry),
        summary=html_to_text(entry.get("summary") or entry.get("description")),
        source_name=source_name,
    )


def split_google_title(title: str, source: str | None) -> tuple[str, str | None]:
    """Sépare « Titre - Éditeur » (format Google Actualités) en (titre, éditeur).

    Si la balise <source> est connue et termine le titre, c'est elle qui fait foi ;
    sinon on coupe sur le dernier « - ».
    """
    if source:
        for separator in (" - ", " \u2013 ", " \u2014 ", " | "):
            suffix = separator + source
            if title.endswith(suffix) and len(title) > len(suffix):
                return title[: -len(suffix)].strip(), source
        return title, source
    head, sep, tail = title.rpartition(" - ")
    if sep and head.strip() and tail.strip():
        return head.strip(), tail.strip()
    return title, None


def parse_google_entry(entry: Any, default_source: str = "Google Actualités") -> FeedEntry | None:
    """Entrée d'une recherche Google Actualités : suffixe « - Éditeur » retiré du titre,
    éditeur utilisé comme source ; résumé ignoré s'il ne fait que répéter le titre."""
    raw_title = html_to_text(entry.get("title"))
    link = (entry.get("link") or "").strip()
    if not raw_title or not link:
        return None
    source_tag = html_to_text((entry.get("source") or {}).get("title")) or None
    title, publisher = split_google_title(raw_title, source_tag)
    summary = html_to_text(entry.get("summary") or entry.get("description"))
    # Le « résumé » Google n'est que le titre suivi de l'éditeur : inutile comme extrait.
    if fold(summary).startswith(fold(title)[:60]):
        summary = ""
    return FeedEntry(
        title=title,
        link=link,
        published=_entry_date(entry),
        summary=summary,
        source_name=publisher or default_source,
    )
