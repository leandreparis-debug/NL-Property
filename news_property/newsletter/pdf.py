"""Version PDF de la newsletter, à imprimer (lien « voir la version à imprimer » de l'e-mail).

Le PDF est la mise en page « impression » de l'aperçu (QR codes sous les articles, A4). Il
est fabriqué par le navigateur du poste, sans nouvelle dépendance : Edge (installé avec
Windows 10 et 11) ou Chrome, lancé en arrière-plan avec --print-to-pdf sur une page HTML
autonome (styles intégrés, images en fichiers locaux).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from flask import render_template
from markupsafe import Markup

from news_property.config import Settings
from news_property.newsletter.qr import qr_svg
from news_property.newsletter.shapes import SHAPES, motif_svg, shape_svg
from news_property.newsletter.theme import SECTION_KEYS, Theme
from news_property.newsletter.view import TRANSACTION_LABEL, NewsletterView

logger = logging.getLogger(__name__)

STYLESHEET = Path(__file__).resolve().parent.parent / "static" / "newsletter.css"
PDF_TIMEOUT_SECONDS = 120
_EXECUTABLES = (
    "msedge",
    "chrome",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


class PdfError(RuntimeError):
    """Le PDF n'a pas pu être fabriqué (message en français)."""


def browser_candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """Emplacements habituels d'Edge et de Chrome (Windows d'abord), puis le PATH."""
    env = os.environ if env is None else env
    found: list[Path] = []
    for variable, *parts in (
        ("ProgramFiles(x86)", "Microsoft", "Edge", "Application", "msedge.exe"),
        ("ProgramFiles", "Microsoft", "Edge", "Application", "msedge.exe"),
        ("ProgramFiles", "Google", "Chrome", "Application", "chrome.exe"),
        ("ProgramFiles(x86)", "Google", "Chrome", "Application", "chrome.exe"),
        ("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
    ):
        base = env.get(variable)
        if base:
            found.append(Path(base, *parts))
    for name in _EXECUTABLES:
        located = shutil.which(name)
        if located:
            found.append(Path(located))
    return found


def find_browser(settings: Settings, env: Mapping[str, str] | None = None) -> Path | None:
    """Navigateur à utiliser : CP_BROWSER s'il est indiqué, sinon le premier trouvé."""
    if settings.browser is not None:
        return settings.browser
    return next((path for path in browser_candidates(env) if path.is_file()), None)


def print_html(view: NewsletterView, theme: Theme, settings: Settings) -> str:
    """Page HTML autonome de la version imprimable (à appeler dans un contexte Flask).

    Styles intégrés ; logos et photos appelés par des adresses file:// locales."""
    accents = {key: theme.accent(key) for key in SECTION_KEYS}
    icons = {
        key: Markup(shape_svg(SHAPES[index % len(SHAPES)], accents[key].color, 22))
        for index, key in enumerate(SECTION_KEYS)
    }
    cache = settings.exports_dir / "qr"
    return render_template(
        "newsletter/print.html",
        view=view,
        theme=theme,
        medium="print",
        theme_css=Markup(theme.css_variables()),  # valeurs validées au chargement
        stylesheet=Markup(STYLESHEET.read_text(encoding="utf-8")),  # fichier de l'application
        qr={url: Markup(qr_svg(url, cache)) for url in view.article_urls},
        photo_src={name: (settings.photos_dir / name).as_uri() for name in view.photo_files},
        logo_src=theme.logo.resolve().as_uri(),
        logo_reverse_src=theme.logo_reverse.resolve().as_uri(),
        accents=accents,
        icons=icons,
        front_icons=tuple(icons[f"rubrique_{n}"] for n in (4, 5, 1)),
        motif=Markup(motif_svg(theme.colors["background"], opacity=0.9)),
        transaction_label=TRANSACTION_LABEL,
        show_other_photos=True,
    )


def browser_command(browser: Path, html: Path, pdf: Path, profile: Path) -> list[str]:
    """Ligne de commande du navigateur (profil à part : un Edge déjà ouvert ne gêne pas).

    Sous Linux en administrateur (serveur de test), Chrome exige --no-sandbox."""
    root_on_linux = sys.platform.startswith("linux") and os.geteuid() == 0
    return [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={profile}",
        "--allow-file-access-from-files",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf}",
        *(["--no-sandbox"] if root_on_linux else []),
        html.as_uri(),
    ]


def make_pdf(html: str, browser: Path, work_dir: Path) -> bytes:
    """Fabrique le PDF de la page HTML avec le navigateur ; renvoie son contenu.

    Lève PdfError (message en français) si le navigateur échoue ou ne produit rien."""
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_dir, prefix="pdf-") as tmp:
        folder = Path(tmp)
        page, pdf = folder / "newsletter.html", folder / "newsletter.pdf"
        page.write_text(html, encoding="utf-8")
        command = browser_command(browser, page, pdf, folder / "profil")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=PDF_TIMEOUT_SECONDS,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfError(
                f"Le navigateur n'a pas fini le PDF en {PDF_TIMEOUT_SECONDS} secondes."
            ) from exc
        except OSError as exc:
            raise PdfError(f"Impossible de lancer le navigateur {browser} : {exc}.") from exc
        data = pdf.read_bytes() if pdf.is_file() else b""
    if not data.startswith(b"%PDF"):
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        logger.warning("PDF non produit par %s : %s", browser, detail[0])
        raise PdfError(f"Le navigateur {browser.name} n'a pas produit de PDF.")
    return data


def newsletter_pdf(view: NewsletterView, theme: Theme, settings: Settings) -> bytes:
    """PDF à imprimer de la newsletter. Lève PdfError si aucun navigateur n'est trouvé ou si
    la fabrication échoue (l'e-mail est alors préparé sans lien vers la version à imprimer)."""
    browser = find_browser(settings)
    if browser is None:
        raise PdfError(
            "Aucun navigateur Edge ou Chrome trouvé pour fabriquer le PDF : indiquez-le dans "
            "CP_BROWSER (fichier .env)."
        )
    return make_pdf(print_html(view, theme, settings), browser, settings.exports_dir)
