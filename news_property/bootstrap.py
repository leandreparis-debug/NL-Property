"""Préparation de la base au démarrage (application web et outils en ligne de commande)."""

from __future__ import annotations

import logging
from contextlib import closing
from pathlib import Path

from news_property.actor_catalog import ACTORS_FILE
from news_property.config import Settings
from news_property.db import connection as db_connection
from news_property.repositories import actors

logger = logging.getLogger(__name__)


def prepare_database(settings: Settings, actors_file: Path | None = None) -> int:
    """Applique les migrations manquantes puis synchronise le référentiel des acteurs.

    Renvoie la version du schéma. Lève MigrationError ou ActorCatalogError (messages en
    français) si l'une des deux étapes échoue.
    """
    version = db_connection.init_db(settings)
    with closing(db_connection.get_connection(settings)) as conn:
        sync = actors.sync_from_yaml(conn, actors_file or ACTORS_FILE)
    logger.info(
        "Base %s (schéma v%d) ; acteurs ajoutés : %d, mis à jour : %d, plus suivis : %d",
        db_connection.db_path(settings),
        version,
        len(sync.added),
        len(sync.updated),
        len(sync.untracked),
    )
    return version
