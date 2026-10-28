"""Configuration de l'application, lue depuis les variables d'environnement et le fichier .env."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 5000
MIN_PORT = 1024
MAX_PORT = 65535
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DATA_SUBDIRS = ("db", "photos", "exports", "reports", "logs")

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"

_TRUE_VALUES = {"1", "true", "yes", "on", "oui", "vrai"}
_FALSE_VALUES = {"0", "false", "no", "off", "non", "faux"}


class ConfigError(ValueError):
    """Erreur de configuration, avec un message en français destiné à l'utilisateur."""


@dataclass(frozen=True)
class Settings:
    """Paramètres immuables de l'application.

    Attributs :
        data_dir : dossier des données locales (base, photos, exports, rapports, logs).
        host : adresse d'écoute, toujours 127.0.0.1 (application locale uniquement).
        port : port d'écoute du serveur web.
        debug : active le mode debug de Flask (à ne jamais activer en usage normal).
        open_browser : ouvre le navigateur au lancement.
        log_level : niveau de journalisation (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        drive_dir : dossier Google Drive synchronisé sur le poste (facultatif) où copier
            le fichier d'e-mail lu par le script Apps Script ; None = copie manuelle.
        browser : navigateur (Edge ou Chrome) qui fabrique la version PDF à imprimer ;
            None = recherche automatique aux emplacements habituels.
    """

    data_dir: Path
    host: str = LOCALHOST
    port: int = DEFAULT_PORT
    debug: bool = False
    open_browser: bool = True
    log_level: str = "INFO"
    drive_dir: Path | None = None
    browser: Path | None = None

    def __post_init__(self) -> None:
        """Vérifie les invariants qui ne dépendent pas de la source de configuration."""
        if self.host != LOCALHOST:
            raise ConfigError(
                f"L'application doit écouter uniquement sur {LOCALHOST} (reçu : {self.host!r})."
            )
        if not MIN_PORT <= self.port <= MAX_PORT:
            raise ConfigError(
                f"Le port doit être compris entre {MIN_PORT} et {MAX_PORT} (reçu : {self.port})."
            )
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(
                f"Niveau de log inconnu : {self.log_level!r}. "
                f"Valeurs possibles : {', '.join(LOG_LEVELS)}."
            )

    @property
    def db_dir(self) -> Path:
        """Dossier de la base de données."""
        return self.data_dir / "db"

    @property
    def photos_dir(self) -> Path:
        """Dossier des photos importées."""
        return self.data_dir / "photos"

    @property
    def exports_dir(self) -> Path:
        """Dossier des exports (e-mail HTML, version imprimable)."""
        return self.data_dir / "exports"

    @property
    def reports_dir(self) -> Path:
        """Dossier des rapports (collecte, etc.)."""
        return self.data_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        """Dossier des fichiers de journalisation."""
        return self.data_dir / "logs"


def default_data_dir(env: Mapping[str, str], platform: str = sys.platform) -> Path:
    """Renvoie le dossier de données par défaut selon le système.

    Windows : %LOCALAPPDATA%\\NewsProperty (hors de Documents, souvent synchronisé
    par OneDrive). Autres systèmes : $XDG_DATA_HOME/news-property, soit par défaut
    ~/.local/share/news-property.
    """
    if platform == "win32":
        local_app_data = env.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "NewsProperty"
    xdg_data_home = env.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "news-property"


def ensure_data_dir(data_dir: Path) -> None:
    """Crée le dossier de données et ses sous-dossiers, puis vérifie l'accès en écriture.

    Lève ConfigError si le dossier ne peut pas être créé ou n'est pas accessible en écriture.
    """
    try:
        for name in DATA_SUBDIRS:
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=data_dir):
            pass
    except OSError as exc:
        raise ConfigError(
            f"Le dossier de données {data_dir} n'est pas accessible en écriture "
            f"(variable CP_DATA_DIR) : {exc.strerror or exc}."
        ) from exc


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Construit les paramètres à partir des variables CP_*.

    Si env est None, lit le fichier .env du projet puis les variables d'environnement
    du système (ces dernières sont prioritaires). Le dossier de données est créé
    avec ses sous-dossiers.

    Lève ConfigError (sous-classe de ValueError) avec un message en français
    nommant la variable fautive si une valeur est invalide.
    """
    if env is None:
        file_values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
        env = {**file_values, **os.environ}

    raw_data_dir = _get(env, "CP_DATA_DIR")
    data_dir = Path(raw_data_dir).expanduser() if raw_data_dir else default_data_dir(env)
    data_dir = data_dir.resolve()

    settings = Settings(
        data_dir=data_dir,
        port=_parse_port(env),
        debug=_parse_bool(env, "CP_DEBUG", default=False),
        open_browser=_parse_bool(env, "CP_OPEN_BROWSER", default=True),
        log_level=_parse_log_level(env),
        drive_dir=_parse_drive_dir(env),
        browser=_parse_browser(env),
    )
    ensure_data_dir(settings.data_dir)
    return settings


def _parse_drive_dir(env: Mapping[str, str]) -> Path | None:
    """Lit CP_DRIVE_DIR : dossier existant (jamais créé : c'est un dossier Google Drive)."""
    raw = _get(env, "CP_DRIVE_DIR")
    if raw is None:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ConfigError(
            f"CP_DRIVE_DIR : le dossier {path} n'existe pas. Indiquez le dossier Google Drive "
            "synchronisé sur ce poste (ex. G:\\Mon Drive\\News Property), ou laissez la "
            "variable vide pour copier le fichier à la main."
        )
    return path.resolve()


def _parse_browser(env: Mapping[str, str]) -> Path | None:
    """Lit CP_BROWSER : chemin du programme Edge ou Chrome (fichier existant)."""
    raw = _get(env, "CP_BROWSER")
    if raw is None:
        return None
    path = Path(raw.strip('"')).expanduser()
    if not path.is_file():
        raise ConfigError(
            f"CP_BROWSER : le fichier {path} n'existe pas. Indiquez le programme Edge ou Chrome "
            "(ex. C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe), "
            "ou laissez la variable vide pour une recherche automatique."
        )
    return path


def _get(env: Mapping[str, str], name: str) -> str | None:
    """Renvoie la valeur nettoyée d'une variable, ou None si absente ou vide."""
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_port(env: Mapping[str, str]) -> int:
    """Lit et valide CP_PORT."""
    raw = _get(env, "CP_PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(
            f"CP_PORT doit être un nombre entier entre {MIN_PORT} et {MAX_PORT} (reçu : {raw!r})."
        ) from None
    if not MIN_PORT <= port <= MAX_PORT:
        raise ConfigError(
            f"CP_PORT doit être compris entre {MIN_PORT} et {MAX_PORT} (reçu : {port})."
        )
    return port


def _parse_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    """Lit et valide une variable booléenne (true/false, 1/0, oui/non, yes/no, on/off)."""
    raw = _get(env, name)
    if raw is None:
        return default
    value = raw.lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"{name} doit valoir true ou false (ou 1/0, oui/non) ; valeur illisible : {raw!r}."
    )


def _parse_log_level(env: Mapping[str, str]) -> str:
    """Lit et valide CP_LOG_LEVEL."""
    raw = _get(env, "CP_LOG_LEVEL")
    if raw is None:
        return "INFO"
    level = raw.upper()
    if level not in LOG_LEVELS:
        raise ConfigError(
            f"CP_LOG_LEVEL inconnu : {raw!r}. Valeurs possibles : {', '.join(LOG_LEVELS)}."
        )
    return level
