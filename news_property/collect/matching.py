"""Détection des acteurs suivis et des termes de l'immobilier logistique."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from news_property.collect.text import fold
from news_property.models import RUBRIC_ACTORS, RUBRIC_MARKET, RUBRIC_REGULATION, Actor

# Limites de mot : ni lettre/chiffre avant, ni lettre/chiffre après.
_BEFORE = r"(?<![\w])"
_AFTER = r"(?![\w])"


def _phrase_pattern(phrase: str, *, case_sensitive: bool, plural: bool = False) -> str:
    """Motif regex d'une expression (sur texte déjà replié par fold).

    Les espaces de l'expression acceptent n'importe quel espacement ; l'apostrophe est
    facultative (« l'entrepôt » trouve aussi « l entrepôt »). case_sensitive :
    la première lettre doit être en majuscule, le reste est insensible à la casse.
    """
    folded = fold(phrase, keep_case=case_sensitive)
    parts: list[str] = []
    for char in folded:
        if char == " ":
            parts.append(r"\s+")
        elif char == "'":
            parts.append(r"(?:'\s*|\s+)?")
        else:
            parts.append(re.escape(char))
    if case_sensitive and folded and folded[0].isalpha():
        head, tail = parts[0], "".join(parts[1:])
        body = f"{head}(?i:{tail})" if tail else head
    else:
        body = "".join(parts)
        if case_sensitive:
            body = f"(?i:{body})"
    if plural:
        body += r"(?:s|x)?"
    return _BEFORE + body + _AFTER


@dataclass(frozen=True)
class ActorMatch:
    """Acteur trouvé et position de la première occurrence."""

    actor: Actor
    position: int


class ActorMatcher:
    """Repère les acteurs suivis (nom ou alias) dans un texte.

    Ignore la casse, les accents et les variantes d'apostrophe ; respecte les limites de
    mots. Pour un acteur case_sensitive, la majuscule initiale est exigée.
    """

    def __init__(self, actors: Sequence[Actor]) -> None:
        """Compile un motif par acteur suivi (tracked)."""
        self._patterns: list[tuple[Actor, re.Pattern[str], bool]] = []
        for actor in actors:
            if not actor.tracked:
                continue
            variants = sorted({actor.name, *actor.aliases}, key=len, reverse=True)
            pattern = "|".join(
                _phrase_pattern(v, case_sensitive=actor.case_sensitive) for v in variants
            )
            flags = 0 if actor.case_sensitive else re.IGNORECASE
            self._patterns.append((actor, re.compile(pattern, flags), actor.case_sensitive))

    def find_all(self, text: str) -> list[Actor]:
        """Tous les acteurs cités dans le texte, dans l'ordre d'apparition."""
        lowered, kept = fold(text), fold(text, keep_case=True)
        found: list[tuple[int, Actor]] = []
        for actor, pattern, case_sensitive in self._patterns:
            match = pattern.search(kept if case_sensitive else lowered)
            if match:
                found.append((match.start(), actor))
        return [actor for _, actor in sorted(found, key=lambda item: item[0])]

    def find(self, text: str) -> ActorMatch | None:
        """Premier acteur cité dans le texte (le plus tôt), ou None."""
        best: ActorMatch | None = None
        lowered, kept = fold(text), fold(text, keep_case=True)
        for actor, pattern, case_sensitive in self._patterns:
            match = pattern.search(kept if case_sensitive else lowered)
            if match and (best is None or match.start() < best.position):
                best = ActorMatch(actor=actor, position=match.start())
        return best


class TermMatcher:
    """Repère des expressions (insensible à la casse et aux accents, pluriel en s/x accepté)."""

    def __init__(self, terms: Sequence[str]) -> None:
        """Compile un motif unique pour toutes les expressions."""
        originals: dict[str, str] = {}
        for term in terms:
            if term.strip():
                originals.setdefault(fold(term), term.strip())
        variants = sorted(originals, key=len, reverse=True)
        self.terms = tuple(variants)
        self._each = [
            (originals[v], re.compile(_phrase_pattern(v, case_sensitive=False, plural=True), re.I))
            for v in variants
        ]
        self._pattern = (
            re.compile(
                "|".join(_phrase_pattern(v, case_sensitive=False, plural=True) for v in variants),
                re.IGNORECASE,
            )
            if variants
            else None
        )

    def find(self, text: str) -> str | None:
        """Première expression trouvée (forme repliée), ou None."""
        if self._pattern is None:
            return None
        match = self._pattern.search(fold(text))
        return match.group(0) if match else None

    def find_all(self, text: str) -> list[str]:
        """Expressions trouvées (telles qu'écrites dans la configuration), dans l'ordre
        d'apparition, sans doublon ; une expression incluse dans une plus longue déjà
        trouvée au même endroit n'est pas répétée."""
        folded = fold(text)
        hits: list[tuple[int, int, str]] = []
        for original, pattern in self._each:  # du plus long au plus court
            for match in pattern.finditer(folded):
                start, end = match.span()
                if not any(s <= start and end <= e for s, e, _ in hits):
                    hits.append((start, end, original))
        ordered = sorted(hits)
        return list(dict.fromkeys(original for _, _, original in ordered))

    def remove(self, text: str) -> str:
        """Texte replié dont toutes les occurrences des expressions ont été retirées."""
        if self._pattern is None:
            return fold(text)
        return self._pattern.sub(" ", fold(text))


@dataclass(frozen=True)
class Classification:
    """Rubrique attribuée à une entrée, avec l'acteur (rubrique 2) ou le terme trouvé
    (réglementation en rubrique 3, immobilier logistique en rubrique 1)."""

    rubric: int
    actor: Actor | None = None
    matched: str = ""


def classify(
    title: str,
    summary: str,
    actors: ActorMatcher,
    topic_terms: TermMatcher,
    exclusions: TermMatcher,
    regulation_terms: TermMatcher | None = None,
) -> Classification | None:
    """Classe une entrée de flux d'après son titre et son résumé.

    1. Terme de réglementation présent (loi, décret, ICPE, PLU...), une fois retirées les
       expressions d'exclusion → rubrique 3.
    2. Sinon, acteur suivi cité (titre d'abord, puis résumé) → rubrique 2.
    3. Sinon, terme de l'immobilier logistique présent (après exclusions, « entrepôt de
       données »...) → rubrique 1.
    4. Sinon → None (entrée écartée).
    """
    cleaned = exclusions.remove(f"{title} . {summary}")
    if regulation_terms is not None:
        term = regulation_terms.find(cleaned)
        if term:
            return Classification(RUBRIC_REGULATION, None, term)
    for text in (title, summary):
        found = actors.find(text)
        if found:
            return Classification(RUBRIC_ACTORS, found.actor, found.actor.name)
    term = topic_terms.find(cleaned)
    if term:
        return Classification(RUBRIC_MARKET, None, term)
    return None
