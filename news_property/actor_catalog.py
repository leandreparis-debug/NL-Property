"""Chargement et validation du référentiel des acteurs suivis (config/acteurs.yaml)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from news_property.config import PROJECT_DIR, ConfigError

ACTORS_FILE = PROJECT_DIR / "config" / "acteurs.yaml"
ALLOWED_KEYS = frozenset({"name", "category", "aliases", "case_sensitive"})


class ActorCatalogError(ConfigError):
    """Référentiel des acteurs invalide (message en français, avec la ligne fautive)."""


@dataclass(frozen=True)
class ActorEntry:
    """Acteur tel que décrit dans le fichier YAML."""

    name: str
    category: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    case_sensitive: bool = False  # majuscule initiale exigée lors de la détection
    line: int = 0  # ligne du fichier, pour les messages d'erreur


def slugify(text: str) -> str:
    """Identifiant ASCII en minuscules (« Kuehne+Nagel » → « kuehne-nagel »)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _normalize_key(text: str) -> str:
    """Forme de comparaison des noms et alias (casse, accents, apostrophes typographiques)."""
    text = text.replace("’", "'").casefold()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").strip()


def _entry_lines(text: str) -> list[int]:
    """Numéro de ligne (à partir de 1) de chaque élément de la liste « actors »."""
    root = yaml.compose(text)
    if not isinstance(root, yaml.MappingNode):
        return []
    for key, value in root.value:
        if getattr(key, "value", None) == "actors" and isinstance(value, yaml.SequenceNode):
            return [item.start_mark.line + 1 for item in value.value]
    return []


def parse_actor_catalog(text: str, source: str = "config/acteurs.yaml") -> list[ActorEntry]:
    """Analyse et valide le contenu YAML du référentiel.

    Règles : liste « actors » non vide ; nom obligatoire ; catégorie texte ; alias liste de
    textes ; un nom ou alias ne peut désigner qu'un seul acteur (comparaison sans tenir
    compte de la casse, des accents ni du type d'apostrophe).
    Lève ActorCatalogError avec la ligne et l'acteur fautifs.
    """
    try:
        data = yaml.safe_load(text)
        lines = _entry_lines(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f", ligne {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ActorCatalogError(f"{source}{where} : YAML illisible ({problem}).") from exc

    if not isinstance(data, dict) or not isinstance(data.get("actors"), list) or not data["actors"]:
        raise ActorCatalogError(
            f"{source} : le fichier doit contenir une liste « actors: » non vide."
        )

    entries: list[ActorEntry] = []
    seen: dict[str, str] = {}  # forme normalisée → description de la première occurrence
    for index, item in enumerate(data["actors"]):
        line = lines[index] if index < len(lines) else 0
        label = f"acteur n°{index + 1}"
        where = f"{source}, ligne {line} ({label})" if line else f"{source} ({label})"
        if not isinstance(item, dict):
            raise ActorCatalogError(f"{where} : chaque acteur doit avoir « name: ».")
        unknown = sorted(set(item) - ALLOWED_KEYS)
        if unknown:
            raise ActorCatalogError(
                f"{where} : champ(s) inconnu(s) {', '.join(map(str, unknown))} "
                "(champs possibles : name, category, aliases, case_sensitive)."
            )
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ActorCatalogError(f"{where} : le nom de l'acteur est obligatoire.")
        name = name.strip()
        where = f"{source}, ligne {line} (« {name} »)" if line else f"{source} (« {name} »)"
        category = item.get("category", "")
        if category is None:
            category = ""
        if not isinstance(category, str):
            raise ActorCatalogError(f"{where} : la catégorie doit être un texte.")
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list) or not all(
            isinstance(a, str) and a.strip() for a in aliases
        ):
            raise ActorCatalogError(f"{where} : « aliases » doit être une liste de textes.")
        aliases = [a.strip() for a in aliases]
        case_sensitive = item.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise ActorCatalogError(f"{where} : « case_sensitive » doit valoir true ou false.")
        if not slugify(name):
            raise ActorCatalogError(f"{where} : le nom doit contenir des lettres ou chiffres.")

        # Les variantes d'un même acteur (casse, accents, apostrophe typographique)
        # sont permises ; un même nom ou alias ne peut pas désigner deux acteurs.
        own_keys: dict[str, str] = {}
        for value, kind in [(name, "le nom"), *((a, "l'alias") for a in aliases)]:
            key = _normalize_key(value)
            if key in seen:
                raise ActorCatalogError(
                    f"{where} : doublon, {kind} « {value} » est déjà utilisé ({seen[key]})."
                )
            own_keys.setdefault(key, f"par « {name} »" + (f", ligne {line}" if line else ""))
        seen.update(own_keys)
        entries.append(
            ActorEntry(
                name=name,
                category=category.strip(),
                aliases=tuple(aliases),
                case_sensitive=case_sensitive,
                line=line,
            )
        )

    slugs = [slugify(e.name) for e in entries]
    for entry, slug in zip(entries, slugs, strict=True):
        if slugs.count(slug) > 1:
            raise ActorCatalogError(
                f"{source}, ligne {entry.line} (« {entry.name} ») : ce nom est trop proche "
                f"d'un autre acteur (même identifiant « {slug} »)."
            )
    return entries


def load_actor_catalog(path: Path = ACTORS_FILE) -> list[ActorEntry]:
    """Lit et valide le fichier du référentiel des acteurs."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ActorCatalogError(
            f"Impossible de lire le référentiel des acteurs {path} : {exc.strerror or exc}."
        ) from exc
    try:
        source = str(path.relative_to(PROJECT_DIR))
    except ValueError:
        source = str(path)
    return parse_actor_catalog(text, source=source)
