"""Données de la newsletter prêtes à afficher, communes à l'aperçu, à l'e-mail et à l'A4.

Tout est préparé ici (sélection, ordre, dates en français, libellés au singulier ou au
pluriel) : les gabarits ne font qu'afficher. Seuls les articles retenus sont publiés, dans
l'ordre choisi pendant la relecture ; le contenu interne est publié tel que saisi.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace

from news_property.config import Settings
from news_property.content.service import plural
from news_property.detect.reporting import format_sqm
from news_property.models import validate_month
from news_property.newsletter.dates import capitalize_first, format_day, format_month, format_range
from news_property.newsletter.theme import Theme, load_theme
from news_property.repositories import (
    absences,
    actors,
    articles,
    document_links,
    events,
    newsletters,
    visits,
)

TRANSACTION_LABEL = "Transaction"
WIDE_SUMMARY = 80  # résumé de rendez-vous au-delà duquel la carte prend toute la largeur


class EmptyNewsletterError(ValueError):
    """La newsletter n'a encore aucun contenu publiable (message en français)."""


class NewsletterNotFoundError(LookupError):
    """Aucune newsletter pour ce mois."""


def _lines(text: str | None) -> tuple[str, ...]:
    """Texte découpé en lignes (affichées avec des retours à la ligne) ; vide si None."""
    return tuple(text.split("\n")) if text else ()


@dataclass(frozen=True)
class FrontFigure:
    """Chiffre de la Une : valeur, libellé accordé, ancre de la rubrique concernée (None si
    la rubrique est vide, donc absente de la newsletter)."""

    value: int
    label: str
    anchor: str | None


@dataclass(frozen=True)
class ArticleItem:
    """Fiche actualité (rubriques 1 et 2)."""

    title: str
    url: str
    source: str
    actor: str | None
    date_label: str | None
    excerpt: str | None
    transaction: bool
    surface_label: str | None  # « 42 000 m² » si une surface est connue


@dataclass(frozen=True)
class PhotoItem:
    """Photo de visite (nom relatif au dossier photos) et sa légende."""

    file_name: str
    caption: str | None


@dataclass(frozen=True)
class VisitItem:
    """Visite d'entrepôt (rubrique 3) ; main_photo = photo principale."""

    site_name: str
    location: str
    date_label: str | None
    owner: str | None
    surface_label: str | None
    description: str | None
    main_photo: PhotoItem | None
    other_photos: tuple[PhotoItem, ...] = ()

    @property
    def description_lines(self) -> tuple[str, ...]:
        """Description découpée en lignes."""
        return _lines(self.description)


@dataclass(frozen=True)
class EventItem:
    """Rendez-vous du calendrier (rubrique 4) ; summary = résumé (plusieurs lignes
    possibles)."""

    title: str
    date_label: str | None  # « Le 5 novembre 2026 », « Du 17 au 19 novembre 2026 »
    location: str | None
    summary: str | None

    @property
    def summary_lines(self) -> tuple[str, ...]:
        """Résumé découpé en lignes."""
        return _lines(self.summary)

    @property
    def wide(self) -> bool:
        """Vrai si un vrai résumé accompagne le rendez-vous (plusieurs lignes, ou plus de
        WIDE_SUMMARY caractères) : la carte prend toute la largeur."""
        return bool(self.summary) and ("\n" in self.summary or len(self.summary) > WIDE_SUMMARY)  # type: ignore[arg-type]


def event_rows(items: tuple[EventItem, ...]) -> tuple[tuple[EventItem, ...], ...]:
    """Rangées de l'e-mail : un rendez-vous avec résumé occupe seul sa rangée ; les autres
    vont par deux, dans l'ordre choisi."""
    rows: list[tuple[EventItem, ...]] = []
    pending: list[EventItem] = []
    for item in items:
        if item.wide:
            if pending:
                rows.append(tuple(pending))
                pending = []
            rows.append((item,))
        else:
            pending.append(item)
            if len(pending) == 2:
                rows.append(tuple(pending))
                pending = []
    if pending:
        rows.append(tuple(pending))
    return tuple(rows)


@dataclass(frozen=True)
class AbsenceItem:
    """Congé ou absence (rubrique 5)."""

    person: str
    period_label: str  # « Du 2 au 6 novembre 2026 », « Le 12 novembre 2026 »
    reason: str | None


@dataclass(frozen=True)
class DocumentItem:
    """Lien vers un document utile (rubrique 6)."""

    url: str
    label: str


Item = ArticleItem | VisitItem | EventItem | AbsenceItem | DocumentItem


@dataclass(frozen=True)
class Section:
    """Rubrique de la newsletter ; hidden = vide, à masquer dans les exports.

    number est le numéro fixe de la rubrique (1 à 6, comme dans la relecture) ;
    display_number est le numéro affiché au lecteur : les rubriques publiées sont
    renumérotées sans trou (1, 2, 3...) quand une rubrique vide est masquée. None si masquée.
    """

    key: str  # rubrique_1 ... rubrique_6
    number: int
    title: str
    kind: str  # articles, visits, events, absences, documents
    items: tuple[Item, ...] = field(default_factory=tuple)
    display_number: int | None = None

    @property
    def anchor(self) -> str:
        """Ancre HTML de la rubrique (liens du sommaire)."""
        return f"rubrique-{self.number}"

    @property
    def number_label(self) -> str:
        """Numéro affiché sur deux chiffres, comme dans la charte (« 01 ») ; vide si masquée."""
        return f"{self.display_number:02d}" if self.display_number is not None else ""

    @property
    def count(self) -> int:
        """Nombre d'éléments publiés."""
        return len(self.items)

    @property
    def hidden(self) -> bool:
        """Vrai si la rubrique est vide (elle n'est alors pas publiée)."""
        return not self.items

    @property
    def event_rows(self) -> tuple[tuple[EventItem, ...], ...]:
        """Rangées des rendez-vous pour l'e-mail (voir event_rows)."""
        return event_rows(tuple(i for i in self.items if isinstance(i, EventItem)))


@dataclass(frozen=True)
class TocEntry:
    """Ligne du sommaire (number = numéro affiché, sans trou)."""

    number: int
    title: str
    anchor: str
    count: int
    count_label: str
    key: str = ""  # rubrique_1 ... rubrique_6

    @property
    def number_label(self) -> str:
        """Numéro sur deux chiffres (« 01 »)."""
        return f"{self.number:02d}"


@dataclass(frozen=True)
class NewsletterView:
    """Newsletter d'un mois, prête à afficher."""

    month: str
    month_label: str  # « Octobre 2026 »
    front_title: str
    front: tuple[FrontFigure, ...]
    sections: tuple[Section, ...]
    toc: tuple[TocEntry, ...]

    @property
    def visible_sections(self) -> tuple[Section, ...]:
        """Rubriques non vides, dans l'ordre."""
        return tuple(s for s in self.sections if not s.hidden)

    @property
    def article_urls(self) -> tuple[str, ...]:
        """Liens des articles publiés (rubriques 1 et 2), pour préparer les QR codes."""
        return tuple(
            item.url for s in self.sections for item in s.items if isinstance(item, ArticleItem)
        )

    @property
    def photo_files(self) -> tuple[str, ...]:
        """Photos publiées (noms relatifs), photo principale d'abord pour chaque visite."""
        files: list[str] = []
        for section in self.sections:
            for item in section.items:
                if isinstance(item, VisitItem):
                    photos = ((item.main_photo,) if item.main_photo else ()) + item.other_photos
                    files += [p.file_name for p in photos]
        return tuple(files)


def _surface_label(value: int | None) -> str | None:
    """« 42 000 m² », ou None si la surface est inconnue."""
    return format_sqm(value) if value else None


def _items_label(count: int, kind: str) -> str:
    """Libellé du nombre d'éléments pour le sommaire."""
    words = {
        "articles": ("article", "articles"),
        "visits": ("visite", "visites"),
        "events": ("rendez-vous", "rendez-vous"),
        "absences": ("absence", "absences"),
        "documents": ("lien", "liens"),
    }[kind]
    return f"{count} {plural(count, *words)}"


def build_newsletter_view(
    conn: sqlite3.Connection, month: str, settings: Settings, theme: Theme | None = None
) -> NewsletterView:
    """Prépare la newsletter du mois AAAA-MM pour l'aperçu et les exports.

    Ne garde que les articles retenus, dans l'ordre choisi ; marque les rubriques vides
    comme masquées ; calcule la Une (comme front_page_figures) et le sommaire. Lève
    NewsletterNotFoundError si le mois n'existe pas, EmptyNewsletterError si aucune
    rubrique n'a de contenu, ThemeError si le thème est invalide. settings est réservé aux
    exports (dossiers de données).
    """
    month = validate_month(month)
    theme = theme or load_theme()
    newsletter = newsletters.get(conn, month)
    if newsletter is None or newsletter.id is None:
        raise NewsletterNotFoundError(f"Aucune newsletter pour {format_month(month)}.")
    nid = newsletter.id
    actor_names = {a.id: a.name for a in actors.list_all(conn)}
    titles = theme.section_titles

    def article_items(rubric: int) -> tuple[ArticleItem, ...]:
        return tuple(
            ArticleItem(
                title=a.title,
                url=a.url,
                source=a.source_name,
                actor=actor_names.get(a.actor_id) or a.actor_label,
                date_label=format_day(a.published_at) if a.published_at else None,
                excerpt=a.excerpt,
                transaction=a.is_transaction,
                surface_label=_surface_label(a.surface_sqm),
            )
            for a in articles.list_for_rubric(conn, nid, rubric, selected_only=True)
        )

    visit_items = []
    for v in visits.list_for_newsletter(conn, nid):
        assert v.id is not None
        photos = tuple(PhotoItem(p.file_name, p.caption) for p in visits.list_photos(conn, v.id))
        visit_items.append(
            VisitItem(
                site_name=v.site_name,
                location=v.location,
                date_label=format_day(v.visit_date) if v.visit_date else None,
                owner=v.owner_name,
                surface_label=_surface_label(v.surface_sqm),
                description=v.description,
                main_photo=photos[0] if photos else None,
                other_photos=photos[1:],
            )
        )
    event_items = tuple(
        EventItem(
            title=e.title,
            date_label=capitalize_first(format_range(e.event_date, e.end_date))
            if e.event_date
            else None,
            location=e.location,
            summary=e.summary,
        )
        for e in events.list_for_newsletter(conn, nid)
    )
    absence_items = tuple(
        AbsenceItem(
            person=a.person_name,
            period_label=capitalize_first(format_range(a.start_date, a.end_date)),
            reason=a.reason,
        )
        for a in absences.list_for_newsletter(conn, nid)
    )
    document_items = tuple(
        DocumentItem(url=link.url, label=link.display_label)
        for link in document_links.list_for_newsletter(conn, nid)
    )

    raw_sections = (
        Section("rubrique_1", 1, titles["rubrique_1"], "articles", article_items(1)),
        Section("rubrique_2", 2, titles["rubrique_2"], "articles", article_items(2)),
        Section("rubrique_3", 3, titles["rubrique_3"], "visits", tuple(visit_items)),
        Section("rubrique_4", 4, titles["rubrique_4"], "events", event_items),
        Section("rubrique_5", 5, titles["rubrique_5"], "absences", absence_items),
        Section("rubrique_6", 6, titles["rubrique_6"], "documents", document_items),
    )
    sections: list[Section] = []
    shown = 0
    for section in raw_sections:
        if not section.hidden:
            shown += 1
            section = replace(section, display_number=shown)
        sections.append(section)
    if not shown:
        raise EmptyNewsletterError(
            f"La newsletter de {format_month(month)} est encore vide : retenez des articles "
            "ou saisissez le contenu interne (visites, calendrier, absences, documents) "
            "avant de la publier."
        )

    def anchor(*numbers: int) -> str | None:
        """Ancre de la première rubrique publiée parmi celles indiquées."""
        for number in numbers:
            section = sections[number - 1]
            if not section.hidden:
                return section.anchor
        return None

    figures = newsletters.front_page_figures(conn, nid)
    front = (
        FrontFigure(
            figures.visits,
            plural(figures.visits, "visite d'entrepôt", "visites d'entrepôts"),
            anchor(3),
        ),
        FrontFigure(
            figures.events,
            plural(figures.events, "rendez-vous à venir", "rendez-vous à venir"),
            anchor(4),
        ),
        FrontFigure(
            figures.articles,
            plural(figures.articles, "actualité retenue", "actualités retenues"),
            anchor(1, 2),
        ),
    )
    toc = tuple(
        TocEntry(s.display_number, s.title, s.anchor, s.count, _items_label(s.count, s.kind), s.key)
        for s in sections
        if s.display_number is not None
    )
    return NewsletterView(
        month=month,
        month_label=format_month(month),
        front_title=titles["une"],
        front=front,
        sections=tuple(sections),
        toc=toc,
    )
