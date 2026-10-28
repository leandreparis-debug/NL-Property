"""Conversion en texte brut et normalisations utilisées pour les comparaisons."""

from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup

APOSTROPHES = "’‘`´ʼ′"
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?…](?=\s)")
_NON_WORD = re.compile(r"[^\w]+")
# Espace ajoutée par la conversion entre une balise et la ponctuation qui la suit
# (« <b>mot</b>. » → « mot . ») ; les espaces de la typographie française (: ; ! ?) restent.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,)\]])")
_DROPPED_TAGS = ("script", "style", "noscript", "template", "iframe", "svg", "object")


def html_to_text(value: str | None) -> str:
    """Convertit un fragment HTML (ou du texte) en texte brut sur une ligne.

    Balises, scripts et styles retirés, entités décodées, espaces (y compris insécables)
    normalisés. Le résultat ne contient plus aucune balise HTML.
    """
    if not value:
        return ""
    if "<" in value or "&" in value:
        soup = BeautifulSoup(value, "html.parser")
        for tag in soup(_DROPPED_TAGS):
            tag.decompose()
        value = _SPACE_BEFORE_PUNCT.sub(r"\1", soup.get_text(" "))
    # Un « < » isolé (texte du type « a < b ») a survécu : on l'échappe en le retirant
    # des chevrons pour qu'aucune balise ne puisse être reconstituée.
    value = value.replace("<", "‹").replace(">", "›")
    return normalize_spaces(value)


def normalize_spaces(value: str) -> str:
    """Remplace toute suite d'espaces (tabulations, retours, insécables) par une espace."""
    return _SPACES.sub(" ", value.replace(" ", " ").replace("​", "")).strip()


def strip_accents(value: str) -> str:
    """Retire les accents (« entrepôt » → « entrepot »)."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def unify_apostrophes(value: str) -> str:
    """Remplace les apostrophes typographiques par « ' »."""
    return value.translate({ord(c): "'" for c in APOSTROPHES})


def fold(value: str, *, keep_case: bool = False) -> str:
    """Forme de comparaison : sans accents, apostrophes unifiées, espaces normalisés,
    en minuscules sauf si keep_case."""
    value = normalize_spaces(unify_apostrophes(strip_accents(value)))
    return value if keep_case else value.casefold()


def title_key(title: str) -> str:
    """Forme d'un titre pour mesurer la similarité (ponctuation retirée)."""
    return normalize_spaces(_NON_WORD.sub(" ", fold(title)))


def truncate_excerpt(text: str, limit: int = 300) -> str:
    """Coupe un texte vers limit caractères, sur une fin de phrase si possible, sinon sur
    une fin de mot, et termine par « ... ». Le texte est conservé tel quel (jamais reformulé).
    """
    text = normalize_spaces(text)
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends and ends[-1] >= limit * 0.5:
        cut = window[: ends[-1]]
    else:
        cut = window[:limit]
        if " " in cut:
            cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" .,;:-\u2013\u2014") + "..."
