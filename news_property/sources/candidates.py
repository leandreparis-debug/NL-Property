"""Sources de veille candidates et sujets interrogés sur Google Actualités (données uniquement).

Les sites ci-dessous sont des médias connus de l'immobilier d'entreprise et de la
logistique ; seule leur page d'accueil publique est indiquée. Aucune adresse de flux n'est
inventée : c'est le diagnostic (diagnostiquer_sources.bat) qui cherche les flux RSS/Atom de
chaque site, les teste et propose la configuration à valider (config/sources.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from news_property.actor_catalog import ACTORS_FILE, load_actor_catalog


@dataclass(frozen=True)
class CandidateSite:
    """Site d'actualité candidat, dont on cherche le flux RSS/Atom."""

    name: str
    site_url: str


CANDIDATE_SITES: tuple[CandidateSite, ...] = (
    CandidateSite("Business Immo", "https://www.businessimmo.com"),
    CandidateSite("CFNEWS Immo", "https://www.cfnewsimmo.net"),
    CandidateSite("Immoweek", "https://www.immoweek.fr"),
    CandidateSite("Voxlog", "https://www.voxlog.fr"),
    CandidateSite("Supply Chain Magazine", "https://www.supplychainmagazine.fr"),
    CandidateSite("Stratégies Logistique", "https://www.strategieslogistique.com"),
    CandidateSite("Actu Transport Logistique", "https://www.actu-transport-logistique.fr"),
    CandidateSite("Le Moniteur", "https://www.lemoniteur.fr"),
    CandidateSite("Batiactu", "https://www.batiactu.com"),
)

GOOGLE_NEWS_NAME = "Google Actualités"
GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_PARAMS = "hl=fr&gl=FR&ceid=FR:fr"
GOOGLE_NEWS_QUERY_TEMPLATE = '"{subject}" when:30d'

# Thèmes recherchés sur Google Actualités, en plus des acteurs du référentiel.
GOOGLE_NEWS_TOPICS: tuple[str, ...] = (
    "immobilier logistique",
    "entrepôt logistique",
    "plateforme logistique",
    "parc logistique",
)


def tracked_actor_names(path: Path = ACTORS_FILE) -> tuple[str, ...]:
    """Noms des acteurs suivis (config/acteurs.yaml), interrogés un par un sur Google
    Actualités. Lève ActorCatalogError si le fichier est invalide."""
    return tuple(entry.name for entry in load_actor_catalog(path))


def google_news_subjects(path: Path = ACTORS_FILE) -> tuple[str, ...]:
    """Sujets Google Actualités : les thèmes, puis les acteurs suivis."""
    return GOOGLE_NEWS_TOPICS + tracked_actor_names(path)
