"""Repère « transaction » (bail, acquisition, livraison d'un actif logistique) et surface."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from news_property.collect.matching import TermMatcher
from news_property.collect.text import fold
from news_property.detect.rules import SurfaceRules, TransactionRules


@dataclass(frozen=True)
class TransactionMatch:
    """Phrase qui justifie le repère et termes trouvés."""

    sentence: str
    terms: tuple[str, ...]
    kind: str  # "strong" (expression forte) ou "action+asset"


class TransactionDetector:
    """Applique les règles du repère « transaction » phrase par phrase."""

    def __init__(self, rules: TransactionRules) -> None:
        """Prépare les listes de termes."""
        self.action = TermMatcher(rules.action_terms)
        self.asset = TermMatcher(rules.asset_terms)
        self.strong = TermMatcher(rules.strong_terms)
        self.exclusions = TermMatcher(rules.exclusions)

    def check_sentence(self, sentence: str) -> TransactionMatch | None:
        """Résultat pour une phrase : expression forte seule, ou terme d'action ET terme
        d'actif dans la même phrase ; une phrase d'exclusion est ignorée."""
        if self.exclusions.find_all(sentence):
            return None
        strong = self.strong.find_all(sentence)
        if strong:
            return TransactionMatch(sentence, tuple(strong), "strong")
        action = self.action.find_all(sentence)
        if not action:
            return None
        asset = self.asset.find_all(sentence)
        if not asset:
            return None
        return TransactionMatch(sentence, tuple(action + asset), "action+asset")

    def detect(self, sentences: Sequence[str]) -> TransactionMatch | None:
        """Première phrase (titre d'abord) qui justifie le repère, ou None."""
        for sentence in sentences:
            match = self.check_sentence(sentence)
            if match:
                return match
        return None

    def terms_in(self, sentence: str) -> list[str]:
        """Tous les termes des trois listes présents dans la phrase (pour repérer les oublis)."""
        found = self.strong.find_all(sentence) + self.action.find_all(sentence)
        return list(dict.fromkeys(found + self.asset.find_all(sentence)))


class SurfaceExtractor:
    """Repère les surfaces écrites dans le texte (« 40 000 m² », « 12.500 m2 »)."""

    # Nombre : chiffres, éventuellement groupés par 3 avec espace (y compris insécable ou
    # fine), point ou apostrophe ; décimales ignorées (« 12,5 » donne 12).
    _NUMBER = r"(?P<number>\d{1,3}(?:[   .']\d{3})+|\d+)(?:[,.]\d+)?"

    def __init__(self, rules: SurfaceRules) -> None:
        """Compile le motif « nombre + unité » (unités comparées sans accents ni casse)."""
        units = sorted({fold(unit) for unit in rules.surface_units}, key=len, reverse=True)
        unit_pattern = "|".join(re.escape(unit).replace(r"\ ", r"\s+") for unit in units)
        self._pattern = re.compile(
            self._NUMBER + r"\s*(?:" + unit_pattern + r")(?![\w])", re.IGNORECASE
        )
        self.max_sqm = rules.max_sqm

    def find_all(self, sentence: str) -> list[int]:
        """Surfaces (m²) de la phrase, dans l'ordre d'apparition, plafond appliqué."""
        values: list[int] = []
        for match in self._pattern.finditer(fold(sentence)):
            digits = re.sub(r"\D", "", match.group("number"))
            if digits:
                value = int(digits)
                if 0 < value <= self.max_sqm:
                    values.append(value)
        return values

    def largest(self, sentences: Sequence[str]) -> int | None:
        """Plus grande surface trouvée dans les phrases, ou None."""
        found = [value for sentence in sentences for value in self.find_all(sentence)]
        return max(found) if found else None
