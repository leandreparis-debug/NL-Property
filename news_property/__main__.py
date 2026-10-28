"""Point d'entrée : python -m news_property."""

from __future__ import annotations

import logging
import socket
import sys
import threading
import webbrowser

from news_property import create_app
from news_property.config import ConfigError, load_settings
from news_property.db import DatabaseError
from news_property.logging_setup import configure_logging
from news_property.version import __version__

logger = logging.getLogger("news_property")

BROWSER_DELAY_SECONDS = 1.0


def is_port_free(host: str, port: int) -> bool:
    """Indique si le port peut être réservé sur l'hôte donné."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Sous Windows, empêche de partager un port déjà utilisé par une autre application.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def port_in_use_message(port: int) -> str:
    """Message affiché lorsque le port est déjà occupé."""
    return (
        f"Le port {port} est déjà utilisé. L'application tourne peut-être déjà : "
        f"ouvrez http://127.0.0.1:{port} dans votre navigateur.\n"
        "Sinon, fermez le programme qui utilise ce port ou choisissez un autre port "
        "(variable CP_PORT dans le fichier .env)."
    )


def main() -> int:
    """Charge la configuration, prépare les journaux, ouvre le navigateur et lance le serveur.

    Renvoie le code de sortie du processus (0 si tout s'est bien passé).
    """
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2

    log_path = configure_logging(settings)
    url = f"http://{settings.host}:{settings.port}"
    logger.info("Démarrage de News Property %s", __version__)
    # AppData est caché dans l'Explorateur : le chemin exact est affiché à chaque démarrage.
    logger.info("Dossier de données : %s", settings.data_dir)
    logger.info("Journal : %s", log_path)

    if not is_port_free(settings.host, settings.port):
        logger.error(port_in_use_message(settings.port))
        return 1

    try:
        app = create_app(settings)
    except (ConfigError, DatabaseError) as exc:
        logger.error("Démarrage impossible : %s", exc)
        return 2

    if settings.open_browser:
        timer = threading.Timer(BROWSER_DELAY_SECONDS, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()

    logger.info("Application disponible sur %s (Ctrl+C pour arrêter)", url)
    try:
        app.run(
            host=settings.host,
            port=settings.port,
            debug=settings.debug,
            use_reloader=False,
        )
    except OSError:
        logger.error(port_in_use_message(settings.port))
        return 1
    except SystemExit as exc:
        # Werkzeug quitte via sys.exit(1) si le port est pris entre-temps.
        if exc.code:
            logger.error(port_in_use_message(settings.port))
            return 1
    except KeyboardInterrupt:
        pass
    logger.info("Arrêt de News Property")
    return 0


if __name__ == "__main__":
    sys.exit(main())
