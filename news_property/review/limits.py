"""Limites éditoriales par rubrique (config/limites.yaml), relues à chaque affichage."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from news_property.config import PROJECT_DIR

LIMITS_FILE = PROJECT_DIR / "config" / "limites.yaml"


@dataclass(frozen=True)
class Limits:
    """Nombre maximal conseillé d'éléments par rubrique (avertissement, jamais blocage)."""

    rubrique_1: int = 6  # Actualités de l'immobilier logistique (articles retenus)
    rubrique_2: int = 8  # Les acteurs du marché (articles retenus)
    rubrique_3: int = 4  # Réglementation (articles retenus)
    visites: int = 4  # Visites d'entrepôts
    photos_par_visite: int = 3  # Photos par visite (refus au-delà)
    evenements: int = 8  # Calendrier à venir
    absences: int = 12  # Congés et absences
    pages_max: int = 4  # Pages A4 de la version imprimée

    def for_rubric(self, rubric: int) -> int:
        """Limite d'articles retenus pour la rubrique 1, 2 ou 3."""
        return {1: self.rubrique_1, 2: self.rubrique_2, 3: self.rubrique_3}[rubric]


DEFAULT_LIMITS = Limits()


def load_limits(path: Path = LIMITS_FILE) -> tuple[Limits, str | None]:
    """Lit config/limites.yaml.

    Renvoie (limites, message d'erreur). Fichier absent : valeurs par défaut, sans
    erreur. Fichier invalide : valeurs par défaut et un message en français à afficher.
    """
    if not path.is_file():
        return DEFAULT_LIMITS, None
    where = f"Fichier {path.name}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f" (ligne {mark.line + 1})" if mark is not None else ""
        return DEFAULT_LIMITS, f"{where} illisible{line} : les limites par défaut sont utilisées."
    if data is None:
        return DEFAULT_LIMITS, None
    if not isinstance(data, dict):
        return DEFAULT_LIMITS, f"{where} invalide : les limites par défaut sont utilisées."
    known = {f.name for f in fields(Limits)}
    unknown = sorted(str(k) for k in data if k not in known)
    if unknown:
        return DEFAULT_LIMITS, (
            f"{where} : clé(s) inconnue(s) {', '.join(unknown)} (clés possibles : "
            f"{', '.join(sorted(known))}). Les limites par défaut sont utilisées."
        )
    values: dict[str, int] = {}
    for key, value in data.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            return DEFAULT_LIMITS, (
                f"{where} : « {key} » doit être un nombre entier entre 1 et 100 "
                f"(valeur lue : {value!r}). Les limites par défaut sont utilisées."
            )
        values[key] = value
    return Limits(**values), None
