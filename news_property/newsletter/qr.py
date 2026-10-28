"""QR codes des liens d'articles (SVG en ligne pour l'impression, PNG pour l'e-mail).

Correction d'erreur M (≈ 15 %), marge de 2 modules. Les fichiers produits sont mis en
cache, nommés d'après l'empreinte SHA-256 de l'URL, dans <données>/exports/qr/.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from news_property.models import validate_url

BORDER = 2


def _checked(url: str) -> str:
    """URL http(s) obligatoire (ValueError sinon) : aucun QR vers un autre type de lien."""
    return validate_url(url, "lien du QR code")


def url_digest(url: str) -> str:
    """Empreinte SHA-256 (hexadécimale) de l'URL, utilisée comme nom de fichier de cache."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def qr_matrix(url: str) -> list[list[bool]]:
    """Matrice du QR code de l'URL, marge comprise (True = module noir)."""
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=BORDER)
    code.add_data(_checked(url))
    code.make(fit=True)
    return [list(row) for row in code.get_matrix()]


def _render_svg(matrix: list[list[bool]]) -> str:
    """SVG autonome (sans en-tête XML) : un chemin, un carré de 1 unité par module noir."""
    size = len(matrix)
    commands = "".join(
        f"M{x} {y}h1v1h-1z" for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'class="qr" role="img" aria-label="QR code du lien" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path d="{commands}" fill="#000000"/></svg>'
    )


def qr_svg(url: str, cache_dir: Path | None = None) -> str:
    """SVG en ligne du QR code de l'URL (lève ValueError si l'URL n'est pas http(s)).

    Avec cache_dir, le SVG est lu dans le cache s'il existe, sinon produit puis enregistré.
    """
    url = _checked(url)
    cached = cache_dir / f"{url_digest(url)}.svg" if cache_dir else None
    if cached is not None and cached.is_file():
        return cached.read_text(encoding="utf-8")
    svg = _render_svg(qr_matrix(url))
    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(svg, encoding="utf-8")
    return svg


def qr_png(
    url: str, path: Path | None = None, box_size: int = 6, cache_dir: Path | None = None
) -> Path:
    """Image PNG du QR code (pour l'e-mail) ; renvoie son chemin.

    path : fichier à écrire ; à défaut, le fichier de cache
    <cache_dir>/<sha256>-<box_size>.png (réutilisé s'il existe).
    """
    url = _checked(url)
    if path is None:
        if cache_dir is None:
            raise ValueError("Indiquez un fichier ou un dossier de cache pour le QR code.")
        path = cache_dir / f"{url_digest(url)}-{box_size}.png"
        if path.is_file():
            return path
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=BORDER, box_size=box_size)
    code.add_data(url)
    code.make(fit=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    code.make_image(fill_color="black", back_color="white").save(path)
    return path
