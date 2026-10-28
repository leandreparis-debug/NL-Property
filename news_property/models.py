"""Modèles de données de la newsletter (dataclasses validées à la construction).

Règle du projet : aucun lien inventé. Toute URL stockée commence par http:// ou https://
(contrôle ici en Python, et contrainte CHECK en SQL).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

MONTH_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

NEWSLETTER_STATUSES = ("draft", "reviewed", "exported")
RUBRIC_MARKET = 1  # actualités de l'immobilier logistique
RUBRIC_ACTORS = 2  # les acteurs du marché (article citant un acteur suivi)
RUBRIC_REGULATION = 3  # réglementation (loi, décret, ICPE, urbanisme, fiscalité...)
ARTICLE_RUBRICS = (RUBRIC_MARKET, RUBRIC_ACTORS, RUBRIC_REGULATION)
RUN_STATUSES = ("running", "success", "partial", "failed")
RUN_FINAL_STATUSES = ("success", "partial", "failed")
BODY_TEXT_MAX_LENGTH = 20_000
EVIDENCE_MAX_LENGTH = 400
ACTOR_LABEL_MAX_LENGTH = 100
TRANSACTION_SOURCES = ("auto", "manual")


def clip_evidence(text: str | None) -> str | None:
    """Phrase justificative limitée à 400 caractères (coupée avec « ... » si besoin)."""
    if text is None:
        return None
    text = " ".join(text.split())
    if len(text) <= EVIDENCE_MAX_LENGTH:
        return text
    return text[: EVIDENCE_MAX_LENGTH - 3].rstrip() + "..."


# --------------------------------------------------------------------------- validation


def validate_month(month: str) -> str:
    """Vérifie un mois de newsletter au format AAAA-MM et le renvoie.

    Lève ValueError (message en français) si le format ou le mois est invalide.
    """
    if not isinstance(month, str) or not MONTH_PATTERN.match(month.strip()):
        raise ValueError(
            f"Mois de newsletter invalide : {month!r}. Format attendu : AAAA-MM (ex. 2026-10)."
        )
    return month.strip()


def validate_url(url: str | None, field_name: str = "url") -> str:
    """Vérifie qu'une URL est absolue et commence par http:// ou https://, et la renvoie.

    Lève ValueError si l'URL est vide ou d'un autre type (aucun lien inventé ni relatif).
    """
    value = url.strip() if isinstance(url, str) else ""
    parts = urlsplit(value)
    if not value or parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError(
            f"Lien invalide ({field_name}) : {url!r}. "
            "Le lien doit être une adresse réelle commençant par http:// ou https://."
        )
    return value


def _require_text(value: str, label: str) -> str:
    """Vérifie qu'un texte obligatoire n'est pas vide et le renvoie nettoyé."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Le champ « {label} » est obligatoire.")
    return value.strip()


def _check_position(position: int | None) -> None:
    """Vérifie qu'une position d'affichage est un entier positif ou nul (ou None)."""
    if position is not None and (not isinstance(position, int) or position < 0):
        raise ValueError(f"Position invalide : {position!r} (entier positif attendu).")


# --------------------------------------------------------------------------- modèles


@dataclass
class Newsletter:
    """Newsletter d'un mois donné (AAAA-MM). La Une n'est jamais stockée : elle est
    calculée à partir des rubriques."""

    month: str
    status: str = "draft"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le mois et le statut."""
        self.month = validate_month(self.month)
        if self.status not in NEWSLETTER_STATUSES:
            raise ValueError(
                f"Statut de newsletter invalide : {self.status!r} "
                f"(attendu : {', '.join(NEWSLETTER_STATUSES)})."
            )


@dataclass
class Actor:
    """Acteur du référentiel (config/acteurs.yaml). tracked=False si retiré du YAML."""

    name: str
    slug: str
    category: str = ""
    aliases: list[str] = field(default_factory=list)
    tracked: bool = True
    case_sensitive: bool = False  # True : majuscule initiale exigée lors de la détection
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le nom et le slug."""
        self.name = _require_text(self.name, "nom de l'acteur")
        self.slug = _require_text(self.slug, "slug de l'acteur")


@dataclass
class Article:
    """Actualité de veille (rubrique 1 : immobilier logistique ; rubrique 2 : acteurs ;
    rubrique 3 : réglementation).

    url est l'adresse réelle de l'article d'origine (obligatoire). position=None à l'ajout
    signifie « à la fin de la rubrique ».
    """

    newsletter_id: int
    rubric: int
    title: str
    source_name: str
    url: str
    actor_id: int | None = None
    actor_label: str | None = None  # acteur « Autre » saisi à la main (hors référentiel)
    # Ajout manuel d'un lien que le site refuse de laisser lire automatiquement :
    # l'utilisateur a confirmé l'avoir ouvert dans son navigateur.
    link_verified_by_user: bool = False
    published_at: datetime | None = None
    excerpt: str | None = None
    body_text: str | None = None  # texte brut de l'article, jamais affiché tel quel
    # Repère « transaction » : None = jamais évalué, "auto" = posé par la détection,
    # "manual" = modifié à la main (la détection n'y touche plus).
    is_transaction: bool = False
    transaction_source: str | None = None
    transaction_evidence: str | None = None  # phrase justificative (≤ 400 caractères)
    transaction_terms: list[str] = field(default_factory=list)
    surface_sqm: int | None = None  # surface en m² citée dans l'article, si connue
    selected: bool = True
    position: int | None = None
    collected_at: datetime | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide la rubrique, le titre, la source, l'URL, la surface et la position."""
        if self.body_text is not None:
            self.body_text = self.body_text[:BODY_TEXT_MAX_LENGTH]
        if self.transaction_source not in (None, *TRANSACTION_SOURCES):
            raise ValueError(
                f"Origine du repère transaction invalide : {self.transaction_source!r}."
            )
        self.transaction_evidence = clip_evidence(self.transaction_evidence)
        if self.rubric not in ARTICLE_RUBRICS:
            raise ValueError(f"Rubrique d'article invalide : {self.rubric!r} (1, 2 ou 3).")
        self.title = _require_text(self.title, "titre")
        self.source_name = _require_text(self.source_name, "source")
        self.url = validate_url(self.url)
        if self.actor_label is not None:
            self.actor_label = self.actor_label.strip() or None
        if self.actor_label is not None and len(self.actor_label) > ACTOR_LABEL_MAX_LENGTH:
            raise ValueError(
                f"Nom d'acteur trop long ({ACTOR_LABEL_MAX_LENGTH} caractères au plus)."
            )
        if self.surface_sqm is not None and (
            not isinstance(self.surface_sqm, int) or self.surface_sqm <= 0
        ):
            raise ValueError(
                f"Surface invalide : {self.surface_sqm!r} (nombre entier de m², ou vide)."
            )
        _check_position(self.position)


@dataclass
class CollectionRun:
    """Exécution de la collecte de veille pour une newsletter."""

    newsletter_id: int
    started_at: datetime
    status: str = "running"
    finished_at: datetime | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le statut."""
        if self.status not in RUN_STATUSES:
            raise ValueError(
                f"Statut de collecte invalide : {self.status!r} "
                f"(attendu : {', '.join(RUN_STATUSES)})."
            )


# --------------------------------------------------------------------------- contenu interne

DEFAULT_DOCUMENT_LABEL = "Voir le document"
MAX_DOCUMENT_LINKS = 6  # rubrique 7 : jusqu'à 6 documents utiles
DOCUMENT_LABEL_MAX_LENGTH = 100


def validate_photo_file_name(file_name: str) -> str:
    """Vérifie qu'un nom de photo est relatif au dossier photos (jamais un chemin absolu).

    Les sous-dossiers sont acceptés avec « / » (ex. « 2026-10/visite.jpg »).
    """
    value = (file_name or "").strip()
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or ".." in value
        or value.endswith("/")
    ):
        raise ValueError(
            f"Nom de photo invalide : {file_name!r}. Attendu : un nom relatif au dossier "
            "photos (ex. « visite-lyon.jpg »), sans chemin absolu ni « .. »."
        )
    return value


@dataclass
class Visit:
    """Visite d'entrepôt du mois (rubrique 4, saisie interne)."""

    newsletter_id: int
    site_name: str
    location: str
    visit_date: date | None = None
    owner_name: str | None = None  # propriétaire ou exploitant, texte libre
    surface_sqm: int | None = None
    description: str | None = None
    position: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le site, le lieu, la surface et la position."""
        self.site_name = _require_text(self.site_name, "site")
        self.location = _require_text(self.location, "lieu")
        if self.surface_sqm is not None and (
            not isinstance(self.surface_sqm, int) or self.surface_sqm <= 0
        ):
            raise ValueError(
                f"Surface invalide : {self.surface_sqm!r} (nombre entier de m², ou vide)."
            )
        _check_position(self.position)


@dataclass
class VisitPhoto:
    """Photo d'une visite. file_name est relatif à <données>/photos."""

    visit_id: int
    file_name: str
    caption: str | None = None
    position: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le nom de fichier (relatif) et la position."""
        self.file_name = validate_photo_file_name(self.file_name)
        _check_position(self.position)


@dataclass
class Event:
    """Événement du calendrier à venir (rubrique 5, saisie interne)."""

    newsletter_id: int
    title: str
    event_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    summary: str | None = None  # jusqu'à 1 000 caractères, retours à la ligne conservés
    position: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le titre, l'ordre des dates et la position."""
        self.title = _require_text(self.title, "titre")
        if self.event_date and self.end_date and self.end_date < self.event_date:
            raise ValueError("La date de fin est antérieure à la date de début.")
        _check_position(self.position)


@dataclass
class Absence:
    """Congé ou absence d'un membre de l'équipe (rubrique 6, saisie interne)."""

    newsletter_id: int
    person_name: str
    start_date: date
    end_date: date | None = None
    reason: str | None = None  # congés, formation, déplacement...
    position: int | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide le nom, les dates et la position."""
        self.person_name = _require_text(self.person_name, "nom")
        if not isinstance(self.start_date, date):
            raise ValueError("La date de début est obligatoire.")
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("La date de fin est antérieure à la date de début.")
        _check_position(self.position)


@dataclass
class DocumentLink:
    """Lien vers un document utile (rubrique 7), par exemple un tableau de bord partagé.

    position va de 1 à 6 ; label None = « Voir le document ». Seul le format de l'URL est
    vérifié (http/https) : le lien, souvent interne, n'est jamais appelé.
    """

    newsletter_id: int
    position: int
    url: str
    label: str | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Valide la position, l'URL et la longueur du libellé."""
        if not isinstance(self.position, int) or not 1 <= self.position <= MAX_DOCUMENT_LINKS:
            raise ValueError(
                f"Position de lien invalide : {self.position!r} (1 à {MAX_DOCUMENT_LINKS})."
            )
        self.url = validate_url(self.url, "lien du document")
        if self.label is not None:
            self.label = self.label.strip() or None
        if self.label is not None and len(self.label) > DOCUMENT_LABEL_MAX_LENGTH:
            raise ValueError(f"Libellé trop long ({DOCUMENT_LABEL_MAX_LENGTH} caractères au plus).")

    @property
    def display_label(self) -> str:
        """Libellé affiché (libellé par défaut si vide)."""
        return self.label or DEFAULT_DOCUMENT_LABEL


@dataclass(frozen=True)
class FrontPageFigures:
    """Chiffres de la Une, calculés à la volée (jamais stockés)."""

    visits: int = 0
    events: int = 0
    articles: int = 0
