"""Données d'affichage des onglets de contenu interne (Une, visites, calendrier, absences,
documents)."""

from __future__ import annotations

import sqlite3
from typing import Any

from news_property.content import service
from news_property.detect.reporting import format_sqm
from news_property.models import DEFAULT_DOCUMENT_LABEL, MAX_DOCUMENT_LINKS, Newsletter
from news_property.repositories import absences, actors, document_links, events, visits
from news_property.review.limits import Limits

CONTENT_TABS = ("une", "visites", "calendrier", "absences", "documents")
TEMPLATES = {
    "une": "review/une.html",
    "visites": "review/visits.html",
    "calendrier": "review/events.html",
    "absences": "review/absences.html",
    "documents": "review/documents.html",
}


def tab_context(
    conn: sqlite3.Connection, newsletter: Newsletter, tab: str, limits: Limits
) -> dict[str, Any]:
    """Variables propres à un onglet de contenu interne."""
    assert newsletter.id is not None
    if tab == "une":
        return {"figures": service.front_page(conn, newsletter.id), "plural": service.plural}
    if tab == "visites":
        items = visits.list_for_newsletter(conn, newsletter.id)
        return {
            "items": items,
            "photos": {v.id: visits.list_photos(conn, v.id) for v in items},  # type: ignore[arg-type]
            "positions": {v.id: i for i, v in enumerate(items)},
            "total": len(items),
            "count": len(items),
            "limit": limits.visites,
            "max_photos": limits.photos_par_visite,
            "actor_list": actors.list_all(conn, tracked_only=True),
            "date_value": service.date_value,
            "format_sqm": format_sqm,
        }
    if tab == "calendrier":
        items = events.list_for_newsletter(conn, newsletter.id)
        return {
            "items": items,
            "positions": {e.id: i for i, e in enumerate(items)},
            "total": len(items),
            "count": len(items),
            "limit": limits.evenements,
            "date_value": service.date_value,
            "format_period": service.format_period,
        }
    if tab == "absences":
        items = absences.list_for_newsletter(conn, newsletter.id)
        return {
            "items": items,
            "positions": {a.id: i for i, a in enumerate(items)},
            "total": len(items),
            "count": len(items),
            "limit": limits.absences,
            "date_value": service.date_value,
            "format_period": service.format_period,
        }
    links = {
        link.position: link for link in document_links.list_for_newsletter(conn, newsletter.id)
    }
    return {
        "default_document_label": DEFAULT_DOCUMENT_LABEL,
        "document_slots": [
            (position, links.get(position)) for position in range(1, MAX_DOCUMENT_LINKS + 1)
        ],
        "document_links": list(links.values()),
    }
