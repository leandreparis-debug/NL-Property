"""Chargement et validation de config/sources.yaml et config/regles_veille.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from news_property.config import PROJECT_DIR, ConfigError
from news_property.models import validate_url
from news_property.sources.candidates import GOOGLE_NEWS_TOPICS

SOURCES_FILE = PROJECT_DIR / "config" / "sources.yaml"
RULES_FILE = PROJECT_DIR / "config" / "regles_veille.yaml"
LINK_RESOLUTIONS = ("resolved", "google_link_only")

HOW_TO_CREATE_SOURCES = (
    "Pour le créer : lancez diagnostiquer_sources.bat, relisez le fichier "
    "sources.proposed.yaml produit dans le dossier reports des données "
    "(%LOCALAPPDATA%\\NewsProperty\\reports), puis copiez-le en config\\sources.yaml "
    "dans le dossier du projet."
)


class SourcesConfigError(ConfigError):
    """config/sources.yaml absent ou invalide (message en français)."""


class RulesConfigError(ConfigError):
    """config/regles_veille.yaml absent ou invalide (message en français)."""


@dataclass(frozen=True)
class RssSource:
    """Flux RSS/Atom d'un site, à collecter."""

    name: str
    url: str


@dataclass(frozen=True)
class GoogleNewsSettings:
    """Paramètres de la recherche Google Actualités : une requête par thème (topics), puis
    une par acteur suivi de config/acteurs.yaml."""

    query_template: str
    link_resolution: str = "google_link_only"
    topics: tuple[str, ...] = GOOGLE_NEWS_TOPICS


@dataclass(frozen=True)
class SourcesConfig:
    """Sources activées : flux RSS et, éventuellement, Google Actualités."""

    rss: list[RssSource] = field(default_factory=list)
    google_news: GoogleNewsSettings | None = None


@dataclass(frozen=True)
class CollectRules:
    """Règles de tri des entrées (listes de config/regles_veille.yaml)."""

    topic_terms: tuple[str, ...]
    exclusions: tuple[str, ...] = ()


def _read_yaml(path: Path, error: type[ConfigError], missing_hint: str) -> Any:
    """Lit un fichier YAML ; lève l'erreur donnée (message français) en cas de problème."""
    if not path.is_file():
        raise error(f"Fichier introuvable : {path}. {missing_hint}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f", ligne {mark.line + 1}" if mark is not None else ""
        raise error(
            f"{path.name}{where} : YAML illisible ({getattr(exc, 'problem', exc)})."
        ) from exc
    except OSError as exc:
        raise error(f"Impossible de lire {path} : {exc.strerror or exc}.") from exc


def _as_bool(value: Any, where: str, error: type[ConfigError]) -> bool:
    """Valeur booléenne stricte (true/false)."""
    if not isinstance(value, bool):
        raise error(f"{where} : « enabled » doit valoir true ou false.")
    return value


def load_sources(path: Path = SOURCES_FILE) -> SourcesConfig:
    """Lit config/sources.yaml et renvoie uniquement les sources activées (enabled: true).

    Lève SourcesConfigError si le fichier est absent ou invalide, avec la marche à suivre.
    Les champs informatifs (verdict, note, subjects) sont ignorés : Google Actualités est
    interrogé pour chaque thème de « topics » (par défaut ceux du diagnostic) puis pour
    chaque acteur suivi de config/acteurs.yaml.
    """
    data = _read_yaml(path, SourcesConfigError, HOW_TO_CREATE_SOURCES)
    if not isinstance(data, dict):
        raise SourcesConfigError(f"{path.name} : contenu invalide. {HOW_TO_CREATE_SOURCES}")

    rss: list[RssSource] = []
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise SourcesConfigError(f"{path.name} : « sources » doit être une liste.")
    for index, item in enumerate(sources, start=1):
        where = f"{path.name}, source n°{index}"
        if not isinstance(item, dict):
            raise SourcesConfigError(f"{where} : format invalide.")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SourcesConfigError(f"{where} : « name » est obligatoire.")
        where = f"{path.name}, source « {name.strip()} »"
        if not _as_bool(item.get("enabled", False), where, SourcesConfigError):
            continue
        source_type = item.get("type", "rss")
        if source_type != "rss":
            raise SourcesConfigError(
                f"{where} : type « {source_type} » non pris en charge dans « sources » "
                "(seul « rss » ; Google Actualités se règle dans le bloc « google_news »)."
            )
        try:
            url = validate_url(item.get("url"), "url")
        except ValueError as exc:
            raise SourcesConfigError(
                f"{where} : source activée sans adresse de flux valide. Mettez enabled: false "
                "ou indiquez l'URL du flux trouvée par le diagnostic."
            ) from exc
        rss.append(RssSource(name=name.strip(), url=url))

    google: GoogleNewsSettings | None = None
    block = data.get("google_news")
    if block is not None:
        where = f"{path.name}, bloc google_news"
        if not isinstance(block, dict):
            raise SourcesConfigError(f"{where} : format invalide.")
        if _as_bool(block.get("enabled", False), where, SourcesConfigError):
            template = block.get("query_template")
            if not isinstance(template, str) or "{subject}" not in template:
                raise SourcesConfigError(
                    f"{where} : « query_template » doit contenir {{subject}} "
                    "(ex. '\"{subject}\" when:30d')."
                )
            resolution = block.get("link_resolution", "google_link_only")
            if resolution not in LINK_RESOLUTIONS:
                raise SourcesConfigError(
                    f"{where} : « link_resolution » doit valoir resolved ou google_link_only."
                )
            topics = block.get("topics")
            if topics is None:
                topics = list(GOOGLE_NEWS_TOPICS)
            if not isinstance(topics, list) or not all(
                isinstance(topic, str) and topic.strip() for topic in topics
            ):
                raise SourcesConfigError(
                    f"{where} : « topics » doit être une liste de thèmes (textes non vides)."
                )
            google = GoogleNewsSettings(
                query_template=template,
                link_resolution=resolution,
                topics=tuple(topic.strip() for topic in topics),
            )

    if not rss and google is None:
        raise SourcesConfigError(f"{path.name} : aucune source activée (enabled: true).")
    return SourcesConfig(rss=rss, google_news=google)


def _term_list(data: dict[str, Any], key: str, path: Path, required: bool) -> tuple[str, ...]:
    """Liste de textes non vides et sans doublon."""
    values = data.get(key)
    if values is None and not required:
        return ()
    if not isinstance(values, list) or (required and not values):
        raise RulesConfigError(f"{path.name} : « {key} » doit être une liste non vide de textes.")
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise RulesConfigError(f"{path.name} : « {key} » contient une valeur vide ou invalide.")
        key_value = value.strip().casefold()
        if key_value in seen:
            raise RulesConfigError(
                f"{path.name} : « {value.strip()} » est en double dans « {key} »."
            )
        seen.add(key_value)
        result.append(value.strip())
    return tuple(result)


def load_rules(path: Path = RULES_FILE) -> CollectRules:
    """Lit et valide config/regles_veille.yaml (topic_terms, exclusions)."""
    data = _read_yaml(path, RulesConfigError, "Ce fichier fait partie du projet : restaurez-le.")
    if not isinstance(data, dict):
        raise RulesConfigError(f"{path.name} : contenu invalide.")
    unknown = sorted(set(data) - {"topic_terms", "exclusions"})
    if unknown:
        raise RulesConfigError(
            f"{path.name} : clé(s) inconnue(s) {', '.join(map(str, unknown))} "
            "(clés possibles : topic_terms, exclusions)."
        )
    return CollectRules(
        topic_terms=_term_list(data, "topic_terms", path, required=True),
        exclusions=_term_list(data, "exclusions", path, required=False),
    )
