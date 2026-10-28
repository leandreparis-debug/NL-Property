"""Découpage en phrases et transformations de texte à longueur constante."""

from __future__ import annotations

import re
import unicodedata

from news_property.collect.text import APOSTROPHES

# Fin de phrase : ponctuation finale (et guillemet fermant éventuel), espace, puis une
# majuscule ou un chiffre. Le groupe « gap » est l'espace où l'on coupe.
_SENTENCE_END = re.compile(r"[.!?…][»\"”)]*(?P<gap>\s+)(?=[«\"“(]?\s*[A-ZÀ-ÖØ-Þ0-9])")
_ABBREVIATIONS = (
    "M.",
    "Mme.",
    "Mlle.",
    "MM.",
    "Dr.",
    "St.",
    "Ste.",
    "av.",
    "bd.",
    "n°.",
    "etc.",
    "J.",
    "Cie.",
)
_TRANSLATE_APOSTROPHES = {ord(c): "'" for c in APOSTROPHES}


def keep_length_apostrophes(text: str) -> str:
    """Remplace les apostrophes typographiques par « ' » (même longueur)."""
    return text.translate(_TRANSLATE_APOSTROPHES)


def strip_accents_keep_length(text: str) -> str:
    """Retire les accents caractère par caractère, sans changer la longueur du texte
    (les positions restent valables sur le texte d'origine)."""
    return "".join(unicodedata.normalize("NFKD", c)[0] if c.strip() else c for c in text)


def split_sentences(text: str) -> list[str]:
    """Découpe un texte en phrases (retours à la ligne et ponctuation finale).

    Les abréviations courantes (« M. Dupont ») ne coupent pas la phrase. Le texte des
    phrases est conservé tel quel (seuls les espaces de coupure disparaissent).
    """
    sentences: list[str] = []
    for block in re.split(r"\n+", text or ""):
        block = block.strip()
        start = 0
        for match in _SENTENCE_END.finditer(block):
            candidate = block[start : match.start("gap")]
            last_word = candidate.rsplit(" ", 1)[-1]
            if last_word in _ABBREVIATIONS or re.fullmatch(r"[A-Z]\.", last_word):
                continue
            if candidate.strip():
                sentences.append(candidate.strip())
            start = match.end("gap")
        if block[start:].strip():
            sentences.append(block[start:].strip())
    return sentences


def article_sentences(title: str, excerpt: str | None, body_text: str | None) -> list[str]:
    """Phrases à analyser : le titre, puis le texte de l'article (ou, à défaut, ses
    premières lignes, qui en sont un extrait), sans phrase en double."""
    parts = [title]
    parts.append(body_text if body_text and body_text.strip() else (excerpt or ""))
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        for sentence in split_sentences(part):
            key = " ".join(sentence.split()).casefold()
            if key and key not in seen:
                seen.add(key)
                result.append(sentence)
    return result
