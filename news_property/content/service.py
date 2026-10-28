"""Actions du contenu interne : visites et photos, calendrier, absences, documents utiles.

Toutes les saisies sont validées ici ; les erreurs sont des FormError (message en français
rattaché au champ fautif), comme pour la relecture.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from news_property.content.photos import PhotoError, SavedPhoto, process_photo
from news_property.models import (
    MAX_DOCUMENT_LINKS,
    Absence,
    DocumentLink,
    Event,
    Newsletter,
    Visit,
    VisitPhoto,
)
from news_property.repositories import absences, document_links, events, newsletters, visits
from news_property.review.service import (
    FormError,
    date_field,
    multiline_field,
    surface_field,
    text_field,
    url_field,
)

MAX_SITE_NAME = 150
MAX_LOCATION = 150
MAX_OWNER = 100
MAX_DESCRIPTION = 400
MAX_CAPTION = 200
MAX_EVENT_TITLE = 150
MAX_EVENT_LOCATION = 150
# Résumé d'un événement : quelques phrases, retours à la ligne permis.
MAX_SUMMARY = 1000
MAX_PERSON = 100
MAX_REASON = 100
MAX_DOCUMENT_LABEL = 100


# --------------------------------------------------------------------------- Une


@dataclass(frozen=True)
class FrontPage:
    """Chiffres de la Une (calculés)."""

    visits: int
    events: int
    articles: int


def front_page(conn: sqlite3.Connection, newsletter_id: int) -> FrontPage:
    """Chiffres de la Une, toujours calculés (jamais saisis) : visites d'entrepôts,
    événements du calendrier, articles de veille retenus."""
    figures = newsletters.front_page_figures(conn, newsletter_id)
    return FrontPage(figures.visits, figures.events, figures.articles)


def plural(count: int, singular: str, plural_form: str) -> str:
    """Libellé au singulier pour 0 ou 1, au pluriel au-delà (« 1 visite », « 2 visites »)."""
    return singular if count <= 1 else plural_form


def date_value(value: date | None) -> str:
    """Valeur du champ date (AAAA-MM-JJ) ou chaîne vide."""
    return value.isoformat() if value else ""


def format_period(start: date | None, end: date | None) -> str:
    """« 12/10/2026 », « du 12/10/2026 au 15/10/2026 », ou chaîne vide."""
    if start is None:
        return ""
    if end is None or end == start:
        return f"{start:%d/%m/%Y}"
    return f"du {start:%d/%m/%Y} au {end:%d/%m/%Y}"


# --------------------------------------------------------------------------- visites


def _previous_month(month: str) -> str:
    """Mois précédent d'un mois AAAA-MM."""
    year, number = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"


def visit_from_form(
    form: Mapping[str, str], newsletter: Newsletter, base: Visit | None = None
) -> tuple[Visit, str | None]:
    """Visite d'après le formulaire (ajout ou modification) et avertissement éventuel.

    L'avertissement (non bloquant) signale une date de visite hors du mois de la
    newsletter et du mois précédent. Lève FormError si une saisie est invalide.
    """
    assert newsletter.id is not None
    values = {
        "site_name": text_field(form, "site", "Site (entrepôt)", MAX_SITE_NAME, required=True)
        or "",
        "location": text_field(form, "lieu", "Lieu (ville)", MAX_LOCATION, required=True) or "",
        "visit_date": date_field(form, "date", "Date de la visite"),
        "owner_name": text_field(form, "acteur", "Propriétaire ou exploitant", MAX_OWNER),
        "surface_sqm": surface_field(form, "surface"),
        "description": text_field(form, "description", "Description", MAX_DESCRIPTION),
    }
    visit = replace(base, **values) if base else Visit(newsletter_id=newsletter.id, **values)
    warning = None
    if visit.visit_date is not None:
        allowed = {newsletter.month, _previous_month(newsletter.month)}
        if visit.visit_date.strftime("%Y-%m") not in allowed:
            warning = (
                f"La date de la visite ({visit.visit_date:%d/%m/%Y}) ne tombe ni dans le mois "
                "de la newsletter ni dans le mois précédent : vérifiez-la."
            )
    return visit, warning


@dataclass
class UploadResult:
    """Bilan d'un envoi de photos."""

    saved: list[SavedPhoto] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def add_photos(
    conn: sqlite3.Connection,
    visit: Visit,
    files: Sequence[tuple[str, bytes]],
    photos_dir: Path,
    month: str,
    max_photos: int,
) -> UploadResult:
    """Traite et enregistre les photos envoyées (nom d'origine, contenu), dans la limite
    de max_photos par visite. Chaque fichier refusé donne un message ; les autres sont
    enregistrés."""
    assert visit.id is not None
    result = UploadResult()
    existing = len(visits.list_photos(conn, visit.id))
    for name, data in files:
        if not name and not data:
            continue  # champ fichier laissé vide
        if existing + len(result.saved) >= max_photos:
            result.errors.append(
                f"« {name} » n'a pas été ajoutée : {max_photos} photos au maximum par visite."
            )
            continue
        try:
            saved = process_photo(data, photos_dir, month, name)
        except PhotoError as exc:
            result.errors.append(str(exc))
            continue
        visits.add_photo(conn, VisitPhoto(visit_id=visit.id, file_name=saved.file_name))
        result.saved.append(saved)
    if not result.saved and not result.errors:
        result.errors.append("Aucune photo choisie.")
    return result


def set_main_photo(conn: sqlite3.Connection, visit_id: int, photo_id: int) -> None:
    """Fait d'une photo la photo principale (première, utilisée en priorité dans les exports)."""
    visits.reorder_photos(conn, visit_id, [photo_id])


def update_caption(conn: sqlite3.Connection, photo: VisitPhoto, form: Mapping[str, str]) -> None:
    """Enregistre la légende d'une photo (vide autorisé)."""
    assert photo.id is not None
    visits.set_caption(conn, photo.id, text_field(form, "legende", "Légende", MAX_CAPTION))


# --------------------------------------------------------------------------- calendrier


def event_from_form(
    form: Mapping[str, str], newsletter_id: int, base: Event | None = None
) -> Event:
    """Événement d'après le formulaire (ajout ou modification)."""
    start = date_field(form, "date", "Date")
    end = date_field(form, "date_fin", "Date de fin")
    if start is None and end is not None:
        raise FormError("Indiquez la date de début avant la date de fin.", "date")
    if start is not None and end is not None and end < start:
        raise FormError("La date de fin est antérieure à la date de début.", "date_fin")
    values = {
        "title": text_field(form, "titre", "Titre", MAX_EVENT_TITLE, required=True) or "",
        "event_date": start,
        "end_date": end,
        "location": text_field(form, "lieu", "Lieu", MAX_EVENT_LOCATION),
        "summary": multiline_field(form, "resume", "Résumé", MAX_SUMMARY),
    }
    return replace(base, **values) if base else Event(newsletter_id=newsletter_id, **values)


# --------------------------------------------------------------------------- absences


def absence_from_form(
    form: Mapping[str, str], newsletter_id: int, base: Absence | None = None
) -> Absence:
    """Absence d'après le formulaire (ajout ou modification) ; la date de début est obligatoire."""
    start = date_field(form, "date", "Du")
    if start is None:
        raise FormError("Le champ « Du » (date de début) est obligatoire.", "date")
    end = date_field(form, "date_fin", "Au")
    if end is not None and end < start:
        raise FormError("La date de fin est antérieure à la date de début.", "date_fin")
    values = {
        "person_name": text_field(form, "nom", "Nom", MAX_PERSON, required=True) or "",
        "start_date": start,
        "end_date": end,
        "reason": text_field(form, "motif", "Motif", MAX_REASON),
    }
    return replace(base, **values) if base else Absence(newsletter_id=newsletter_id, **values)


# --------------------------------------------------------------------------- documents


def document_links_from_form(form: Mapping[str, str], newsletter: Newsletter) -> list[DocumentLink]:
    """Liens des documents d'après le formulaire : 6 emplacements (lien_1 / libelle_1...).

    Un emplacement sans lien ni libellé est ignoré ; un libellé sans lien est une erreur.
    Seul le format est vérifié (http/https) : les liens, souvent internes (SharePoint,
    Google Drive), ne sont jamais appelés. Lève FormError (champ fautif nommé).
    """
    assert newsletter.id is not None
    links: list[DocumentLink] = []
    for position in range(1, MAX_DOCUMENT_LINKS + 1):
        url_name, label_name = f"lien_{position}", f"libelle_{position}"
        label = text_field(form, label_name, f"Libellé du lien {position}", MAX_DOCUMENT_LABEL)
        if not (form.get(url_name) or "").strip():
            if label:
                raise FormError(
                    f"Lien {position} : indiquez l'adresse du document, ou effacez le libellé.",
                    url_name,
                )
            continue
        url = url_field(form, url_name, f"Lien {position}")
        links.append(
            DocumentLink(newsletter_id=newsletter.id, position=position, url=url, label=label)
        )
    return links


def save_document_links(
    conn: sqlite3.Connection, newsletter: Newsletter, form: Mapping[str, str]
) -> list[DocumentLink]:
    """Enregistre les liens des documents utiles (jusqu'à 6)."""
    assert newsletter.id is not None
    return document_links.replace_all(
        conn, newsletter.id, document_links_from_form(form, newsletter)
    )


def clear_document_links(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    """Retire tous les liens des documents utiles."""
    assert newsletter.id is not None
    document_links.replace_all(conn, newsletter.id, [])


# --------------------------------------------------------------------------- divers


def last_draft(conn: sqlite3.Connection) -> Newsletter | None:
    """Newsletter en cours de préparation : la plus récente au statut Brouillon."""
    return next((n for n in newsletters.list_all(conn) if n.status == "draft"), None)


def content_counts(conn: sqlite3.Connection, newsletter_id: int) -> dict[str, int]:
    """Nombre d'éléments de chaque rubrique interne (visites, calendrier, absences, documents)."""
    return {
        "visites": len(visits.list_for_newsletter(conn, newsletter_id)),
        "calendrier": len(events.list_for_newsletter(conn, newsletter_id)),
        "absences": len(absences.list_for_newsletter(conn, newsletter_id)),
        "documents": document_links.count(conn, newsletter_id),
    }
