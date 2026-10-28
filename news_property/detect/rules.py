"""Chargement et validation de config/regles_detection.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from news_property.config import PROJECT_DIR, ConfigError

DETECTION_RULES_FILE = PROJECT_DIR / "config" / "regles_detection.yaml"
DEFAULT_MAX_SQM = 1_000_000


class DetectionRulesError(ConfigError):
    """Règles de détection invalides (message en français)."""


@dataclass(frozen=True)
class TransactionRules:
    """Listes du repère « transaction »."""

    action_terms: tuple[str, ...]
    asset_terms: tuple[str, ...]
    strong_terms: tuple[str, ...]
    exclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceRules:
    """Unités de surface reconnues et plafond de vraisemblance."""

    surface_units: tuple[str, ...]
    max_sqm: int = DEFAULT_MAX_SQM


@dataclass(frozen=True)
class DetectionRules:
    """Ensemble des règles de détection."""

    transaction: TransactionRules
    surface: SurfaceRules


def _read(path: Path) -> Any:
    """Lit un fichier YAML (erreurs en français)."""
    if not path.is_file():
        raise DetectionRulesError(
            f"Fichier introuvable : {path}. Ce fichier fait partie du projet."
        )
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f", ligne {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or exc
        raise DetectionRulesError(f"{path.name}{where} : YAML illisible ({problem}).") from exc


def _section(data: Any, key: str, where: str) -> dict[str, Any]:
    """Sous-bloc obligatoire."""
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, dict):
        raise DetectionRulesError(f"{where} : le bloc « {key}: » est absent ou invalide.")
    return value


def _strings(
    block: dict[str, Any], key: str, where: str, *, required: bool = True
) -> tuple[str, ...]:
    """Liste de textes non vides, sans doublon (comparaison sans casse)."""
    values = block.get(key)
    if values is None and not required:
        return ()
    if not isinstance(values, list) or (required and not values):
        raise DetectionRulesError(f"{where} : « {key} » doit être une liste non vide de textes.")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DetectionRulesError(
                f"{where} : « {key} » contient une valeur vide ou invalide ({value!r}). "
                'Mettez les mots comme On, Oui, Non, Yes, No entre guillemets ("On").'
            )
        key_value = value.strip().casefold()
        if key_value in seen:
            raise DetectionRulesError(
                f"{where} : « {value.strip()} » est en double dans « {key} »."
            )
        seen.add(key_value)
        result.append(value.strip())
    return tuple(result)


def _check_keys(block: dict[str, Any], allowed: set[str], where: str) -> None:
    """Refuse les clés inconnues (fautes de frappe)."""
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise DetectionRulesError(
            f"{where} : clé(s) inconnue(s) {', '.join(map(str, unknown))} "
            f"(clés possibles : {', '.join(sorted(allowed))})."
        )


def load_detection_rules(path: Path = DETECTION_RULES_FILE) -> DetectionRules:
    """Lit et valide config/regles_detection.yaml.

    Lève DetectionRulesError avec un message clair (liste fautive, valeur invalide).
    """
    data = _read(path)
    where = path.name
    if not isinstance(data, dict):
        raise DetectionRulesError(f"{where} : contenu invalide.")
    _check_keys(data, {"transaction", "surface"}, where)

    block = _section(data, "transaction", where)
    block_where = f"{where}, bloc transaction"
    _check_keys(block, {"action_terms", "asset_terms", "strong_terms", "exclusions"}, block_where)
    transaction = TransactionRules(
        action_terms=_strings(block, "action_terms", block_where),
        asset_terms=_strings(block, "asset_terms", block_where),
        strong_terms=_strings(block, "strong_terms", block_where),
        exclusions=_strings(block, "exclusions", block_where, required=False),
    )

    block = _section(data, "surface", where)
    block_where = f"{where}, bloc surface"
    _check_keys(block, {"surface_units", "max_sqm"}, block_where)
    max_sqm = block.get("max_sqm", DEFAULT_MAX_SQM)
    if not isinstance(max_sqm, int) or isinstance(max_sqm, bool) or max_sqm <= 0:
        raise DetectionRulesError(
            f"{block_where} : « max_sqm » doit être un nombre entier de m² positif."
        )
    surface = SurfaceRules(
        surface_units=_strings(block, "surface_units", block_where), max_sqm=max_sqm
    )
    return DetectionRules(transaction=transaction, surface=surface)
