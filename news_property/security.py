"""Sécurité de l'application locale : clé secrète, CSRF, contrôle du Host et de l'origine.

Même en local, une page web malveillante ouverte dans le navigateur pourrait tenter
d'envoyer des formulaires à l'application (CSRF) ou de la joindre via un nom de domaine
qu'elle contrôle (DNS rebinding). Voir docs/securite.md.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from flask import Flask, abort, render_template, request
from flask.typing import ResponseReturnValue
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from news_property.config import Settings

logger = logging.getLogger(__name__)

SECRET_KEY_FILE = "secret_key"
# Taille maximale d'un envoi de fichiers (photos) : 15 Mo.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

csrf = CSRFProtect()


class HostNotAllowed(BadRequest):
    """Requête adressée à un nom d'hôte qui n'est pas celui de l'application."""

    description = (
        "Adresse non autorisée. Ouvrez l'application à l'adresse http://127.0.0.1:<port> "
        "affichée au démarrage."
    )


def secret_key_path(settings: Settings) -> Path:
    """Fichier de la clé secrète : <données>/secret_key (jamais dans le code ni le dépôt)."""
    return settings.data_dir / SECRET_KEY_FILE


def load_or_create_secret_key(settings: Settings) -> str:
    """Renvoie la clé secrète de l'application, créée au premier lancement.

    La clé (64 caractères hexadécimaux aléatoires) est écrite dans le dossier de données,
    avec des droits restreints quand le système le permet.
    """
    path = secret_key_path(settings)
    try:
        key = path.read_text(encoding="ascii").strip()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    except OSError, UnicodeDecodeError:
        logger.warning("Clé secrète illisible, une nouvelle clé est créée", exc_info=True)
    key = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key)
    logger.info("Clé secrète créée : %s", path)
    return key


def allowed_hosts(port: int) -> frozenset[str]:
    """En-têtes Host acceptés : 127.0.0.1:<port> et localhost:<port>."""
    return frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})


def init_security(app: Flask, settings: Settings) -> None:
    """Installe clé secrète, protection CSRF, contrôle du Host et de l'origine."""
    app.config["SECRET_KEY"] = load_or_create_secret_key(settings)
    app.config.setdefault("CP_ALLOWED_HOSTS", allowed_hosts(settings.port))
    # Jeton valable pour toute la session du navigateur : une page de relecture laissée
    # ouverte longtemps reste utilisable.
    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
    # Affectation explicite : Flask prédéfinit cette clé à None, setdefault ne suffirait pas.
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    # Taille maximale d'un envoi (photos) : 15 Mo ; au-delà, page d'erreur claire.
    if app.config.get("MAX_CONTENT_LENGTH") is None:  # Flask le définit à None par défaut
        app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    csrf.init_app(app)

    @app.before_request
    def check_host_and_origin() -> None:
        """Refuse un Host étranger (DNS rebinding) et un formulaire venant d'un autre site."""
        if request.host not in app.config["CP_ALLOWED_HOSTS"]:
            logger.warning("Requête refusée : Host non autorisé %r", request.host)
            raise HostNotAllowed()
        if request.method in SAFE_METHODS:
            return
        expected = request.host_url.rstrip("/")
        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")
        if origin is not None and origin != expected:
            abort(403)
        if origin is None and referer is not None and not referer.startswith(expected + "/"):
            abort(403)

    @app.errorhandler(CSRFError)
    def csrf_error(error: CSRFError) -> ResponseReturnValue:
        """Formulaire sans jeton valide : page 400 en français."""
        return (
            render_template(
                "error.html",
                code=400,
                title="Formulaire expiré",
                message=(
                    "Le formulaire n'a pas pu être vérifié (page trop ancienne ou ouverte "
                    "depuis un autre site). Revenez en arrière, rechargez la page, puis "
                    "recommencez."
                ),
            ),
            400,
        )

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error: RequestEntityTooLarge) -> ResponseReturnValue:
        """Envoi trop volumineux (photos > 15 Mo au total) : page 413 en français."""
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        referrer = request.referrer or ""
        back = referrer if referrer.startswith(request.host_url) else None
        return (
            render_template(
                "error.html",
                code=413,
                title="Fichiers trop volumineux",
                message=(
                    f"L'envoi dépasse {limit_mb} Mo. Envoyez les photos une par une, ou "
                    "réduisez-les (une photo de téléphone fait en général 2 à 6 Mo)."
                ),
                back_url=back,
            ),
            413,
        )

    @app.errorhandler(HostNotAllowed)
    def host_error(error: HostNotAllowed) -> ResponseReturnValue:
        """Host étranger : page 400 en français."""
        return (
            render_template(
                "error.html", code=400, title="Adresse non autorisée", message=error.description
            ),
            400,
        )
