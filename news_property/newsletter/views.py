"""Pages de la newsletter : aperçu d'un mois, export e-mail, écran « Thème » et logos.

L'aperçu assemble les gabarits communs (templates/newsletter/_*.html) avec medium="print" :
c'est la mise en page qui servira aux exports. Tout ce qui est calculé (QR codes, adresses
des photos, variables CSS du thème) est préparé ici ; les gabarits ne font qu'afficher.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    send_file,
    url_for,
)
from flask.typing import ResponseReturnValue
from markupsafe import Markup

from news_property.collect.periods import PARIS
from news_property.config import Settings
from news_property.db.connection import get_db
from news_property.models import Newsletter, validate_month
from news_property.newsletter.dates import format_day, format_month
from news_property.newsletter.email import (
    GMAIL_CLIP_BYTES,
    GMAIL_CLIP_MARGIN,
    build_email,
    export_folder,
    package_name,
    size_label,
    write_email_export,
)
from news_property.newsletter.pdf import newsletter_pdf
from news_property.newsletter.qr import qr_svg
from news_property.newsletter.shapes import SHAPES, motif_svg, shape_svg
from news_property.newsletter.theme import SECTION_KEYS, THEME_FILE, Theme, ThemeError, load_theme
from news_property.newsletter.view import (
    TRANSACTION_LABEL,
    EmptyNewsletterError,
    NewsletterNotFoundError,
    NewsletterView,
    build_newsletter_view,
)
from news_property.repositories import newsletters
from news_property.review.service import STATUS_LABELS

bp = Blueprint("newsletter", __name__)
logger = logging.getLogger(__name__)


def _settings() -> Settings:
    """Paramètres de l'application."""
    return current_app.config["CP_SETTINGS"]


def _theme_file() -> Path:
    """Fichier de thème (modifiable par les tests via CP_THEME_FILE)."""
    return Path(current_app.config.get("CP_THEME_FILE", THEME_FILE))


def qr_cache_dir(settings: Settings) -> Path:
    """Dossier de cache des QR codes : <données>/exports/qr."""
    return settings.exports_dir / "qr"


def newsletter_context(
    view: NewsletterView,
    theme: Theme,
    settings: Settings,
    medium: str,
    show_other_photos: bool = True,
) -> dict:
    """Variables communes aux gabarits de la newsletter.

    show_other_photos : afficher, en vignettes, les photos autres que la photo principale
    (chaque sortie décide selon la place dont elle dispose).

    qr : URL → SVG en ligne (produit ici, depuis des URL déjà validées http/https) ;
    photo_src : nom de photo → adresse de la photo servie par l'application ;
    accents : rubrique → couleur d'accent et couleur du numéro ; icons : rubrique → forme
    de la charte (SVG) dans sa couleur d'accent ; motif : motif blanc du bandeau.
    Toutes les couleurs des SVG viennent du thème validé (#RRGGBB).
    """
    cache = qr_cache_dir(settings)
    accents = {key: theme.accent(key) for key in SECTION_KEYS}
    # Une forme par rubrique, en alternance (arche, nœud, heptagone).
    icons = {
        key: Markup(shape_svg(SHAPES[index % len(SHAPES)], accents[key].color, 22))
        for index, key in enumerate(SECTION_KEYS)
    }
    # Chiffres de la Une : visites (rubrique 3), rendez-vous (4), actualités (1).
    return {
        "accents": accents,
        "icons": icons,
        "front_icons": tuple(icons[f"rubrique_{n}"] for n in (3, 4, 1)),
        "motif": Markup(motif_svg(theme.colors["background"], opacity=0.9)),
        "view": view,
        "theme": theme,
        "medium": medium,
        "theme_css": Markup(theme.css_variables()),  # valeurs validées au chargement
        "qr": {url: Markup(qr_svg(url, cache)) for url in view.article_urls},  # SVG généré ici
        "photo_src": {
            name: url_for("content.photo_file", file_name=name) for name in view.photo_files
        },
        "logo_src": url_for("newsletter.logo"),
        "logo_reverse_src": url_for("newsletter.logo", variant="blanc"),
        "transaction_label": TRANSACTION_LABEL,
        "show_other_photos": show_other_photos,
    }


def _theme_error_page(exc: ThemeError) -> ResponseReturnValue:
    """Page d'erreur claire quand config/theme.yaml est invalide."""
    return (
        render_template(
            "error.html",
            title="Thème invalide",
            code=500,
            message=f"{exc} Corrigez le fichier, puis rechargez la page.",
            back_url=url_for("newsletter.theme"),
        ),
        500,
    )


@bp.get("/newsletters/<month>/apercu")
def preview(month: str) -> ResponseReturnValue:
    """Aperçu de la newsletter du mois, mise en page « impression » (≈ 800 px, fond blanc)."""
    try:
        month = validate_month(month)
    except ValueError:
        abort(404)
    try:
        theme = load_theme(_theme_file())
    except ThemeError as exc:
        return _theme_error_page(exc)
    settings = _settings()
    try:
        view = build_newsletter_view(get_db(), month, settings, theme)
    except NewsletterNotFoundError:
        abort(404)
    except EmptyNewsletterError as exc:
        return render_template(
            "newsletter/preview_empty.html",
            month=month,
            month_label=format_month(month),
            message=str(exc),
        )
    return render_template(
        "newsletter/preview.html", **newsletter_context(view, theme, settings, "print")
    )


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


# --------------------------------------------------------------------------- export e-mail


@dataclass(frozen=True)
class EmailExportInfo:
    """Résumé du dernier export e-mail d'un mois, lu dans son paquet."""

    created_at: str
    subject: str
    html_size: int
    images_count: int
    images_size: int
    package: Path
    html: Path
    pdf: Path | None  # version à imprimer, ouverte par le lien de l'e-mail (s'il y en a une)

    @property
    def html_size_label(self) -> str:
        """Taille du HTML, lisible."""
        return size_label(self.html_size)

    @property
    def images_size_label(self) -> str:
        """Poids des images, lisible."""
        return size_label(self.images_size)

    @property
    def clipped_risk(self) -> bool:
        """Vrai si Gmail risque de tronquer le message (HTML proche de 102 Ko)."""
        return self.html_size > GMAIL_CLIP_MARGIN


def _export_info(settings: Settings, month: str) -> EmailExportInfo | None:
    """Dernier export e-mail du mois, ou None s'il n'y en a pas (ou s'il est illisible)."""
    folder = export_folder(settings, month)
    package = folder / package_name(month)
    if not package.is_file():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
        images = data["images"]
        created = datetime.fromisoformat(data["createdAt"]).astimezone(PARIS)
        return EmailExportInfo(
            created_at=f"{format_day(created)} à {created:%H:%M}",
            subject=data["subject"],
            html_size=len(data["htmlBody"].encode("utf-8")),
            images_count=len(images),
            images_size=sum(len(base64.b64decode(image["data"])) for image in images),
            package=package,
            html=folder / f"news-property-{month}.html",
            pdf=pdf if (pdf := folder / f"news-property-{month}.pdf").is_file() else None,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _month_or_404(month: str) -> Newsletter:
    """Newsletter du mois ; 404 si le mois est invalide ou inconnu."""
    try:
        month = validate_month(month)
    except ValueError:
        abort(404)
    newsletter = newsletters.get(get_db(), month)
    if newsletter is None:
        abort(404)
    return newsletter


@bp.get("/exports")
def exports() -> ResponseReturnValue:
    """Liste des newsletters à exporter, avec l'état de leur dernier export e-mail."""
    settings = _settings()
    rows = [
        (n, STATUS_LABELS.get(n.status, n.status), _export_info(settings, n.month))
        for n in newsletters.list_all(get_db())
    ]
    return render_template("newsletter/exports.html", rows=rows, format_month=format_month)


@bp.get("/newsletters/<month>/export")
def export_page(month: str) -> ResponseReturnValue:
    """Export du mois : préparer l'e-mail, voir le résultat, marche à suivre pour Gmail."""
    newsletter = _month_or_404(month)
    settings = _settings()
    return render_template(
        "newsletter/export.html",
        newsletter=newsletter,
        month_label=format_month(newsletter.month),
        status_label=STATUS_LABELS.get(newsletter.status, newsletter.status),
        info=_export_info(settings, newsletter.month),
        drive_dir=settings.drive_dir,
        clip_limit=size_label(GMAIL_CLIP_BYTES),
    )


@bp.post("/newsletters/<month>/export/email")
def export_email(month: str) -> ResponseReturnValue:
    """Prépare l'e-mail du mois et son paquet pour le script Apps Script (PRG)."""
    newsletter = _month_or_404(month)
    settings = _settings()
    back = redirect(url_for("newsletter.export_page", month=newsletter.month))
    try:
        theme = load_theme(_theme_file())
    except ThemeError as exc:
        return _theme_error_page(exc)
    try:
        export = build_email(
            get_db(),
            newsletter.month,
            settings,
            theme,
            make_print_pdf=lambda view, current: newsletter_pdf(view, current, settings),
        )
        files = write_email_export(export, settings)
    except EmptyNewsletterError as exc:
        flash(str(exc), "error")
        return back
    except OSError as exc:
        logger.exception("Export e-mail impossible pour %s", newsletter.month)
        flash(f"Impossible d'écrire l'export : {exc.strerror or exc}.", "error")
        return back
    flash(
        f"E-mail préparé : {size_label(export.html_size)} de HTML, "
        f"{len(export.images)} images ({size_label(export.images_size)}).",
        "success",
    )
    if files.drive_copy is not None:
        flash(f"Paquet copié dans Google Drive : {files.drive_copy}.", "success")
    for warning in export.warnings:
        flash(warning, "warning")
    if newsletter.status == "draft":
        flash(
            "La newsletter n'est pas encore marquée comme relue : vérifiez-la avant l'envoi.",
            "warning",
        )
    return back


def _export_file(month: str, *parts: str) -> Path:
    """Fichier de l'export e-mail du mois ; 404 si absent ou si le nom est suspect."""
    newsletter = _month_or_404(month)
    name = parts[-1]
    if not re.fullmatch(r"[a-z0-9-]+\.(png|jpg)", name) and len(parts) > 1:
        abort(404)
    path = export_folder(_settings(), newsletter.month).joinpath(*parts)
    if not path.is_file():
        abort(404)
    return path


@bp.get("/newsletters/<month>/export/email/apercu")
def export_email_preview(month: str) -> ResponseReturnValue:
    """L'e-mail exporté, tel qu'il sera reçu (images servies par l'application)."""
    newsletter = _month_or_404(month)
    return send_file(
        _export_file(newsletter.month, f"news-property-{newsletter.month}.html"),
        mimetype="text/html",
        max_age=0,
    )


@bp.get("/newsletters/<month>/export/email/images/<name>")
def export_email_image(month: str, name: str) -> ResponseReturnValue:
    """Image de l'e-mail exporté (noms produits par l'application uniquement)."""
    return send_file(_export_file(month, "images", name), max_age=0)


@bp.get("/newsletters/<month>/export/email/<name>.pdf")
def export_email_pdf(month: str, name: str) -> ResponseReturnValue:
    """Version PDF à imprimer (ouverte par le lien « voir la version à imprimer »)."""
    newsletter = _month_or_404(month)
    if name != f"news-property-{newsletter.month}":
        abort(404)
    return send_file(
        _export_file(newsletter.month, f"{name}.pdf"), mimetype="application/pdf", max_age=0
    )


@bp.get("/newsletters/<month>/export/email/paquet")
def export_email_package(month: str) -> ResponseReturnValue:
    """Téléchargement du paquet JSON, à déposer dans le dossier Google Drive."""
    newsletter = _month_or_404(month)
    name = package_name(newsletter.month)
    return send_file(
        _export_file(newsletter.month, name),
        mimetype="application/json",
        as_attachment=True,
        download_name=name,
        max_age=0,
    )
