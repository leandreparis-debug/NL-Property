"""Extraction des premières lignes et du texte brut d'une page d'article."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from news_property.collect.text import html_to_text, normalize_spaces, truncate_excerpt
from news_property.models import BODY_TEXT_MAX_LENGTH

EXCERPT_LENGTH = 300
MIN_PARAGRAPH_LENGTH = 40

# Éléments jamais considérés comme contenu de l'article.
_NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "form",
    "button",
    "nav",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
)
# Mots repérés dans les attributs id/class/role des blocs parasites.
_NOISE_WORDS = frozenset(
    {
        "cookie",
        "cookies",
        "consent",
        "gdpr",
        "rgpd",
        "cmp",
        "didomi",
        "tarteaucitron",
        "banner",
        "bandeau",
        "newsletter",
        "subscribe",
        "abonnement",
        "menu",
        "nav",
        "navbar",
        "navigation",
        "footer",
        "header",
        "share",
        "sharing",
        "social",
        "breadcrumb",
        "related",
        "comments",
        "comment",
        "advert",
        "ads",
        "ad",
        "pub",
        "publicite",
        "sidebar",
        "popup",
        "modal",
        "promo",
        "tags",
        "author",
        "toolbar",
        "skip",
    }
)
_TOKEN = re.compile(r"[a-z]+")
_BODY_SELECTORS = (
    "[itemprop=articleBody]",
    "article",
    "[role=main]",
    "main",
    ".article-body, .article__body, .article-content, .article__content, .entry-content,"
    " .post-content, .content-article, .news-content",
)


@dataclass(frozen=True)
class PageContent:
    """Contenu utile d'une page : texte des paragraphes et description des métadonnées."""

    body_text: str
    description: str


def _is_noise(tag: Tag) -> bool:
    """Bloc parasite (bandeau cookies, menu, partage...) d'après id, class et role."""
    attrs = tag.attrs or {}
    values = [attrs.get("id") or "", attrs.get("role") or "", attrs.get("aria-label") or ""]
    classes = attrs.get("class") or []
    values += classes if isinstance(classes, list) else [str(classes)]
    tokens = {t for value in values for t in _TOKEN.findall(str(value).lower())}
    return bool(tokens & _NOISE_WORDS) or attrs.get("aria-hidden") == "true"


def _meta(soup: BeautifulSoup, *names: str) -> str:
    """Première balise meta (property ou name) non vide parmi celles données."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag):
            content = html_to_text(str(tag.get("content") or ""))
            if content:
                return content
    return ""


def extract_page(html: str) -> PageContent:
    """Extrait le texte brut des paragraphes de l'article et la description de la page.

    Ordre de recherche du corps : contenu balisé articleBody, <article>, bloc principal
    (<main>, role=main), blocs de contenu usuels, sinon toute la page ; les menus,
    bandeaux cookies, pieds de page, encarts de partage sont ignorés.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    description = _meta(soup, "og:description", "description", "twitter:description")

    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed or tag.name in ("html", "body", "article", "main"):
            continue
        # Un conteneur qui englobe l'article (ex. class="page with-menu") n'est pas parasite.
        if _is_noise(tag) and tag.find(["article", "main"]) is None:
            tag.decompose()

    paragraphs: list[str] = []
    for selector in (*_BODY_SELECTORS, "body"):
        container = soup.select_one(selector)
        if container is None:
            continue
        paragraphs = [
            text
            for p in container.find_all("p")
            if len(text := normalize_spaces(p.get_text(" "))) >= MIN_PARAGRAPH_LENGTH
        ]
        if paragraphs:
            break
    body = "\n\n".join(paragraphs)[:BODY_TEXT_MAX_LENGTH]
    return PageContent(body_text=body, description=description)


def choose_excerpt(page: PageContent | None, feed_summary: str, limit: int = EXCERPT_LENGTH) -> str:
    """Premières lignes : début du corps de l'article, sinon description de la page,
    sinon résumé du flux ; coupées proprement vers limit caractères. Chaîne vide si rien.
    """
    for candidate in (
        page.body_text if page else "",
        page.description if page else "",
        feed_summary,
    ):
        if candidate and candidate.strip():
            return truncate_excerpt(candidate, limit)
    return ""
