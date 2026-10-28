"""Pages de la newsletter : écran « Thème » (lecture seule) et logos du thème.

L'aperçu du mois et les exports s'ajoutent dans les étapes suivantes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, send_file
from flask.typing import ResponseReturnValue

from news_property.newsletter.theme import SECTION_KEYS, THEME_FILE, ThemeError, load_theme

bp = Blueprint("newsletter", __name__)
logger = logging.getLogger(__name__)


def _theme_file() -> Path:
    """Fichier de thème (modifiable par les tests via CP_THEME_FILE)."""
    return Path(current_app.config.get("CP_THEME_FILE", THEME_FILE))


@bp.get("/theme")
def theme() -> ResponseReturnValue:
    """Écran « Thème » en lecture seule : couleurs, polices, logo, contrastes, fichier."""
    path = _theme_file()
    try:
        current = load_theme(path)
    except ThemeError as exc:
        return render_template("newsletter/theme.html", theme=None, error=str(exc), path=path)
    return render_template(
        "newsletter/theme.html",
        theme=current,
        error=None,
        path=path,
        checks=current.contrast_checks(),
        warnings=current.warnings(),
        accents={key: current.accent(key) for key in SECTION_KEYS},
    )


@bp.get("/theme/logo")
@bp.get("/theme/logo/<variant>")
def logo(variant: str = "couleur") -> ResponseReturnValue:
    """Logo du thème (config/theme.yaml) : « couleur » (logo) ou « blanc » (logo_reverse)."""
    if variant not in ("couleur", "blanc"):
        abort(404)
    try:
        current = load_theme(_theme_file())
    except ThemeError:
        abort(404)
    return send_file(current.logo_reverse if variant == "blanc" else current.logo, max_age=0)
