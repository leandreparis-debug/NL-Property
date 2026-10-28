"""Actions de relecture : validation des saisies, ordre, ajout manuel, statut.

Règles : l'URL d'un article collecté n'est jamais modifiable (elle n'est pas lue dans les
formulaires) ; un lien ajouté à la main doit répondre (2xx après redirections) avant
d'être enregistré ; aucune donnée venant d'un site n'est réécrite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from news_property.collect.excerpt import choose_excerpt, extract_page
from news_property.collect.periods import PARIS
from news_property.collect.text import html_to_text
from news_property.collect.urls import clean_url, url_key
from news_property.http_client import Fetcher
from news_property.models import Actor, Article, validate_url
from news_property.repositories import (
    absences,
    articles,
    document_links,
    events,
    newsletters,
    visits,
)
from news_property.repositories._common import utcnow
from news_property.review.limits import Limits

STATUS_LABELS = {"draft": "Brouillon", "reviewed": "Relue", "exported": "Exportée"}
RUBRIC_TITLES = {
    1: "Rubrique 1 - Actualités de l'immobilier logistique",
    2: "Rubrique 2 - Les acteurs du marché",
}

MAX_TITLE = 300
MAX_EXCERPT = 1000
MAX_SOURCE = 120
MAX_ACTOR_LABEL = 100
MAX_URL = 2000
MAX_SURFACE = 1_000_000
OTHER_ACTOR = "autre"


class FormError(ValueError):
    """Saisie refusée ; le message (en français) est affiché à côté du champ fautif."""

    def __init__(self, message: str, field: str | None = None) -> None:
        """field : nom du champ de formulaire concerné (None si aucun en particulier)."""
        super().__init__(message)
        self.field = field


class LinkError(FormError):
    """Lien ajouté à la main qui ne peut pas être vérifié."""


class LinkRefused(LinkError):
    """Le site répond mais refuse la lecture automatique (403, protection anti-robot).

    L'utilisateur peut alors confirmer lui-même que le lien affiche bien l'article.
    """

    def __init__(self, message: str, field: str | None, final_url: str) -> None:
        """final_url : adresse atteinte (après redirections éventuelles)."""
        super().__init__(message, field)
        self.final_url = final_url


# Réponses d'un site qui existe mais bloque les robots (lecture automatique refusée).
REFUSED_STATUSES = frozenset({401, 403, 406, 429, 503})


# --------------------------------------------------------------------------- vue d'ensemble


@dataclass(frozen=True)
class Counts:
    """Éléments retenus d'une newsletter."""

    selected_1: int = 0
    selected_2: int = 0

    @property
    def total(self) -> int:
        """Total des articles retenus (rubriques 1 et 2)."""
        return self.selected_1 + self.selected_2

    def for_rubric(self, rubric: int) -> int:
        """Articles retenus de la rubrique 1 ou 2."""
        return self.selected_1 if rubric == 1 else self.selected_2


@dataclass(frozen=True)
class NewsletterSummary:
    """Ligne de la liste des newsletters."""

    month: str
    status: str
    counts: Counts

    @property
    def status_label(self) -> str:
        """Statut en français."""
        return STATUS_LABELS.get(self.status, self.status)


def count_selected(conn: sqlite3.Connection, newsletter_id: int) -> Counts:
    """Articles retenus par rubrique."""
    row = conn.execute(
        "SELECT"
        " (SELECT COUNT(*) FROM articles WHERE newsletter_id = :id AND rubric = 1"
        "  AND selected = 1) AS s1,"
        " (SELECT COUNT(*) FROM articles WHERE newsletter_id = :id AND rubric = 2"
        "  AND selected = 1) AS s2",
        {"id": newsletter_id},
    ).fetchone()
    return Counts(selected_1=row["s1"], selected_2=row["s2"])


def list_newsletters(conn: sqlite3.Connection) -> list[NewsletterSummary]:
    """Newsletters de la plus récente à la plus ancienne, avec leurs éléments retenus."""
    return [
        NewsletterSummary(n.month, n.status, count_selected(conn, n.id))  # type: ignore[arg-type]
        for n in newsletters.list_all(conn)
    ]


def review_warnings(conn: sqlite3.Connection, newsletter_id: int, limits: Limits) -> list[str]:
    """Avertissements (non bloquants) avant de marquer la newsletter comme relue."""
    counts = count_selected(conn, newsletter_id)
    warnings = []
    for rubric in (1, 2):
        if counts.for_rubric(rubric) > limits.for_rubric(rubric):
            warnings.append(
                f"{RUBRIC_TITLES[rubric]} : {counts.for_rubric(rubric)} articles retenus pour "
                f"une limite de {limits.for_rubric(rubric)}."
            )
        elif counts.for_rubric(rubric) == 0:
            warnings.append(f"{RUBRIC_TITLES[rubric]} : aucun article retenu, elle sera vide.")
    visit_count = len(visits.list_for_newsletter(conn, newsletter_id))
    if visit_count == 0:
        warnings.append("Aucune visite d'entrepôt saisie : la rubrique 3 sera vide.")
    elif visit_count > limits.visites:
        warnings.append(f"Visites : {visit_count} saisies pour une limite de {limits.visites}.")
    event_count = len(events.list_for_newsletter(conn, newsletter_id))
    if event_count == 0:
        warnings.append("Aucun rendez-vous au calendrier : la rubrique 4 sera vide.")
    elif event_count > limits.evenements:
        warnings.append(
            f"Calendrier : {event_count} rendez-vous pour une limite de {limits.evenements}."
        )
    absence_count = len(absences.list_for_newsletter(conn, newsletter_id))
    if absence_count == 0:
        warnings.append("Aucune absence saisie : la rubrique 5 sera vide.")
    elif absence_count > limits.absences:
        warnings.append(f"Absences : {absence_count} saisies pour une limite de {limits.absences}.")
    if document_links.count(conn, newsletter_id) == 0:
        warnings.append("Pas de lien vers un document utile : la rubrique 6 sera vide.")
    if warnings:
        warnings.append(f"Rappel : la version imprimée doit tenir en {limits.pages_max} pages A4.")
    return warnings


# --------------------------------------------------------------------------- saisies


def text_field(
    form: Mapping[str, str], name: str, label: str, max_length: int, *, required: bool = False
) -> str | None:
    """Texte saisi, nettoyé ; None si vide. FormError si obligatoire et vide, ou trop long."""
    value = " ".join((form.get(name) or "").split())
    if not value:
        if required:
            raise FormError(f"Le champ « {label} » est obligatoire.", name)
        return None
    if len(value) > max_length:
        raise FormError(
            f"Le champ « {label} » est trop long ({len(value)} caractères, {max_length} au plus).",
            name,
        )
    return value


def multiline_field(form: Mapping[str, str], name: str, label: str, max_length: int) -> str | None:
    """Texte sur plusieurs lignes (retours à la ligne conservés) ; None si vide."""
    raw = (form.get(name) or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    value = "\n".join(lines).strip("\n ")
    while "\n\n\n" in value:
        value = value.replace("\n\n\n", "\n\n")
    if not value:
        return None
    if len(value) > max_length:
        raise FormError(
            f"Le champ « {label} » est trop long ({len(value)} caractères, {max_length} au plus).",
            name,
        )
    return value


def date_field(form: Mapping[str, str], name: str, label: str) -> date | None:
    """Date AAAA-MM-JJ (champ date du navigateur) ; None si vide."""
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise FormError(
            f"Le champ « {label} » n'est pas une date valide (JJ/MM/AAAA).", name
        ) from None
    if not 1990 <= value.year <= 2100:
        raise FormError(f"Le champ « {label} » contient une année improbable ({value.year}).", name)
    return value


def surface_field(form: Mapping[str, str], name: str = "surface") -> int | None:
    """Surface en m² : vide autorisé, sinon entier > 0 (espaces de groupement acceptés)."""
    raw = (form.get(name) or "").strip().replace(" ", "").replace(" ", "").replace("\xa0", "")
    if not raw:
        return None
    if not raw.isdigit() or int(raw) == 0 or int(raw) > MAX_SURFACE:
        raise FormError(
            "La surface doit être un nombre entier de m² (ou rester vide).",
            name,
        )
    return int(raw)


def url_field(form: Mapping[str, str], name: str, label: str) -> str:
    """Lien http(s) obligatoire."""
    raw = (form.get(name) or "").strip()
    if not raw:
        raise FormError(f"Le champ « {label} » est obligatoire.", name)
    if len(raw) > MAX_URL:
        raise FormError(f"Le champ « {label} » est trop long.", name)
    try:
        return validate_url(raw, label)
    except ValueError:
        raise FormError(
            f"Le champ « {label} » doit être une adresse commençant par http:// ou https://.",
            name,
        ) from None


def _published_at(current: datetime | None, chosen: date | None) -> datetime | None:
    """Date de publication : l'heure d'origine est gardée si le jour ne change pas ;
    sinon midi, heure de Paris."""
    if chosen is None:
        return None
    if current is not None and current.astimezone(PARIS).date() == chosen:
        return current
    return datetime.combine(chosen, time(12, 0), tzinfo=PARIS).astimezone(UTC)


def _actor_choice(
    form: Mapping[str, str], actors: Sequence[Actor]
) -> tuple[Actor | None, str | None]:
    """Acteur choisi : (acteur suivi, None), (None, texte « Autre ») ou (None, None)."""
    choice = (form.get("acteur") or "").strip()
    if not choice:
        return None, None
    if choice == OTHER_ACTOR:
        label = text_field(form, "acteur_autre", "Autre acteur", MAX_ACTOR_LABEL)
        if label is None:
            raise FormError(
                "Indiquez le nom de l'acteur dans le champ « Autre acteur ».", "acteur_autre"
            )
        return None, label
    actor = next((a for a in actors if str(a.id) == choice), None)
    if actor is None:
        raise FormError("Acteur inconnu.", "acteur")
    return actor, None


def apply_article_form(
    article: Article, form: Mapping[str, str], actors: Sequence[Actor]
) -> Article:
    """Article modifié d'après le formulaire de sa carte.

    L'URL n'est jamais lue dans le formulaire : un champ « url » ajouté à la requête est
    ignoré. « Retenir » ne se règle pas ici (bouton rapide dédié). Lève FormError si une
    saisie est invalide.
    """
    actor, actor_label = _actor_choice(form, actors)
    return replace(
        article,
        is_transaction="transaction" in form,
        actor_id=actor.id if actor else None,
        actor_label=actor_label,
        title=text_field(form, "titre", "Titre", MAX_TITLE, required=True),
        published_at=_published_at(article.published_at, date_field(form, "date", "Date")),
        source_name=text_field(form, "source", "Source", MAX_SOURCE, required=True),
        excerpt=text_field(form, "extrait", "Premières lignes", MAX_EXCERPT),
        surface_sqm=surface_field(form),
        url=article.url,
    )


# --------------------------------------------------------------------------- ordre


def parse_rank(raw: str | None, count: int) -> int:
    """Rang saisi (1 = en tête), borné entre 1 et count ; renvoie l'index (0 = en tête).

    Lève FormError si la saisie n'est pas un nombre entier.
    """
    value = (raw or "").strip()
    if not value.lstrip("-").isdigit():
        raise FormError("Le rang doit être un numéro (1 = en tête de liste).", "rang")
    return min(max(int(value), 1), max(count, 1)) - 1


def _target_index(ids: list[int], item_id: int, action: str, rank: str | None) -> int:
    """Nouvel index d'un élément selon l'action : haut, bas, tete, fin ou rang."""
    index = ids.index(item_id)
    if action == "haut":
        return max(index - 1, 0)
    if action == "bas":
        return min(index + 1, len(ids) - 1)
    if action == "tete":
        return 0
    if action == "fin":
        return len(ids) - 1
    if action == "rang":
        return parse_rank(rank, len(ids))
    raise FormError("Déplacement inconnu.")


def moved_order(ids: list[int], item_id: int, action: str, rank: str | None) -> list[int] | None:
    """Ordre après déplacement ; None si l'élément ne bouge pas."""
    target = _target_index(ids, item_id, action, rank)
    if target == ids.index(item_id):
        return None
    ids.remove(item_id)
    ids.insert(target, item_id)
    return ids


def move_article(
    conn: sqlite3.Connection, article: Article, action: str, rank: str | None = None
) -> bool:
    """Déplace un article dans sa rubrique : « haut », « bas », « tete », « fin » ou
    « rang » (rank = numéro saisi, borné). Renvoie False s'il ne bouge pas."""
    ids = [a.id for a in articles.list_for_rubric(conn, article.newsletter_id, article.rubric)]
    order = moved_order(ids, article.id, action, rank)  # type: ignore[arg-type]
    if order is None:
        return False
    articles.reorder(conn, article.newsletter_id, article.rubric, order)  # type: ignore[arg-type]
    return True


def change_rubric(
    conn: sqlite3.Connection,
    article: Article,
    target: str | None,
    form: Mapping[str, str],
    actors: Sequence[Actor],
) -> Article:
    """Déplace un article vers l'autre rubrique (en fin de liste).

    Vers la rubrique 2 (« Les acteurs du marché »), l'acteur est obligatoire : un des
    acteurs suivis, ou « Autre » avec son nom. Vers la rubrique 1, l'article est conservé
    tel quel, avec son acteur éventuel. Lève FormError sinon.
    """
    if target not in ("1", "2") or int(target) == article.rubric:
        raise FormError("Rubrique de destination invalide.")
    assert article.id is not None
    if target == "1":
        return articles.move_to_rubric(conn, article.id, 1, article.actor_id, article.actor_label)
    actor, label = _actor_choice(form, actors)
    if actor is None and label is None:
        raise FormError(
            "Choisissez l'acteur de l'article pour le déplacer vers la rubrique 2.", "acteur"
        )
    return articles.move_to_rubric(conn, article.id, 2, actor.id if actor else None, label)


def keep_first(conn: sqlite3.Connection, newsletter_id: int, rubric: int, n: int) -> int:
    """Retient les n premiers articles de la rubrique (dans l'ordre affiché) et ne retient
    plus les suivants. Renvoie le nombre d'articles retenus."""
    rubric_articles = articles.list_for_rubric(conn, newsletter_id, rubric)
    for index, article in enumerate(rubric_articles):
        articles.set_selected(conn, article.id, index < n)  # type: ignore[arg-type]
    return min(n, len(rubric_articles))


# --------------------------------------------------------------------------- ajout manuel


@dataclass(frozen=True)
class PagePreview:
    """Informations lues sur la page d'un lien ajouté à la main (pour pré-remplir)."""

    url: str  # adresse finale, après redirections
    title: str
    excerpt: str
    body_text: str
    source_name: str
    published: date | None


def _meta(soup: BeautifulSoup, *names: str) -> str:
    """Contenu de la première balise meta (property ou name) trouvée."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag) and tag.get("content"):
            return html_to_text(str(tag.get("content")))
    return ""


def _published(soup: BeautifulSoup) -> date | None:
    """Date de publication annoncée par la page (article:published_time), si lisible."""
    raw = _meta(soup, "article:published_time", "datePublished", "date")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date() if raw else None
    except ValueError:
        return None


def preview_link(fetcher: Fetcher, url: str) -> PagePreview:
    """Vérifie qu'un lien répond (2xx après redirections) et lit titre et premières lignes.

    Le lien est demandé par l'utilisateur : robots.txt ne bloque pas cette vérification
    (une seule page lue, comme le ferait son navigateur). Lève LinkRefused si le site
    répond mais refuse la lecture automatique (403...), LinkError si le lien est invalide
    ou ne répond pas.
    """
    try:
        url = url_field({"lien": url}, "lien", "Lien")
    except FormError as exc:
        raise LinkError(str(exc), "lien") from None
    result = fetcher.fetch(url, check_robots=False)
    if result.status_code in REFUSED_STATUSES:
        raise LinkRefused(
            f"Le site refuse la lecture automatique de cette page ({result.error}) : "
            "l'application ne peut pas vérifier le lien elle-même.",
            "lien",
            result.final_url or url,
        )
    if not result.ok:
        raise LinkError(f"Le lien ne répond pas correctement : {result.error}", "lien")
    final_url = clean_url(validate_url(result.final_url or url))
    soup = BeautifulSoup(result.text or "", "html.parser")
    title = _meta(soup, "og:title", "twitter:title")
    if not title and isinstance(soup.find("h1"), Tag):
        title = html_to_text(soup.find("h1").get_text(" "))  # type: ignore[union-attr]
    if not title and soup.title is not None:
        title = html_to_text(soup.title.get_text(" "))
    page = extract_page(result.text or "")
    host = (urlsplit(final_url).hostname or "").removeprefix("www.")
    return PagePreview(
        url=final_url,
        title=title[:MAX_TITLE],
        excerpt=choose_excerpt(page, ""),
        body_text=page.body_text,
        source_name=(_meta(soup, "og:site_name") or host)[:MAX_SOURCE],
        published=_published(soup),
    )


def find_duplicate(conn: sqlite3.Connection, newsletter_id: int, url: str) -> Article | None:
    """Article du mois ayant déjà ce lien (après normalisation), ou None."""
    key = url_key(url)
    return next(
        (a for a in articles.list_for_newsletter(conn, newsletter_id) if url_key(a.url) == key),
        None,
    )


def add_manual_article(
    conn: sqlite3.Connection,
    newsletter_id: int,
    preview: PagePreview | None,
    form: Mapping[str, str],
    actors: Sequence[Actor],
    *,
    refused_url: str | None = None,
) -> Article:
    """Enregistre un article ajouté à la main (retenu, en fin de rubrique).

    preview : page lue et vérifiée par l'application. Si le site a refusé la lecture
    automatique (preview None, refused_url = adresse atteinte), l'utilisateur doit avoir
    coché « J'ai ouvert ce lien... » (champ « confirme ») : l'article est alors marqué
    « lien vérifié par l'utilisateur ». Lève FormError si une saisie est invalide, si la
    confirmation manque ou si l'article est déjà dans la newsletter.
    """
    rubric = 2 if form.get("rubrique") == "2" else 1
    if preview is None:
        if refused_url is None:
            raise FormError("Lien non vérifié.", "lien")
        if "confirme" not in form:
            raise FormError(
                "Cochez la case de confirmation : le site refuse la lecture automatique, "
                "l'application ne peut donc pas vérifier ce lien elle-même.",
                "confirme",
            )
        url = clean_url(validate_url(refused_url))
    else:
        url = preview.url
    if find_duplicate(conn, newsletter_id, url) is not None:
        raise FormError("Cet article est déjà dans la newsletter de ce mois.", "lien")
    draft = Article(
        newsletter_id=newsletter_id,
        rubric=rubric,
        title=(preview.title if preview else "") or "-",
        source_name=(preview.source_name if preview else "") or "-",
        url=url,
        body_text=(preview.body_text or None) if preview else None,
        collected_at=utcnow(),
        selected=True,
        link_verified_by_user=preview is None,
    )
    article = apply_article_form(draft, form, actors)
    added = articles.add(conn, article)
    if added is None:
        raise FormError("Cet article est déjà dans la newsletter de ce mois.", "lien")
    return added
