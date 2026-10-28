"""Modèles de données de la newsletter (dataclasses validées à la construction).

Règle du projet : aucun lien inventé. Toute URL stockée commence par http:// ou https://
(contrôle ici en Python, et contrainte CHECK en SQL).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

MONTH_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

NEWSLETTER_STATUSES = ("draft", "reviewed", "exported")
RUBRIC_MARKET = 1  # actualités de l'immobilier logistique
RUBRIC_ACTORS = 2  # les acteurs du marché (article citant un acteur suivi)
ARTICLE_RUBRICS = (RUBRIC_MARKET, RUBRIC_ACTORS)
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
    """Actualité de veille (rubrique 1 : immobilier logistique ; rubrique 2 : acteurs).

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
            raise ValueError(f"Rubrique d'article invalide : {self.rubric!r} (1 ou 2).")
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
