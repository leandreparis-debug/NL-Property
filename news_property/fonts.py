"""Police système pour les images produites par l'application (logo provisoire, photos de
démonstration).

La police intégrée de Pillow ne contient pas les lettres accentuées (« É ») : on cherche
d'abord une police système (Windows, puis Linux, puis macOS).
"""

from __future__ import annotations

from PIL import ImageFont

# Polices système avec accents (Windows, puis Linux, puis macOS).
FONT_CANDIDATES = (
    "arialbd.ttf",
    "arial.ttf",
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial.ttf",
)


def system_font(
    size: int, text: str, ascii_text: str | None = None
) -> tuple[str, ImageFont.ImageFont | ImageFont.FreeTypeFont]:
    """Police système avec accents si disponible, et le texte à écrire avec.

    À défaut, police intégrée de Pillow et texte sans accent (ascii_text, ou text privé de
    ses caractères non ASCII).
    """
    for candidate in FONT_CANDIDATES:
        try:
            return text, ImageFont.truetype(candidate, size)
        except OSError:
            continue
    fallback = ascii_text if ascii_text is not None else text.encode("ascii", "ignore").decode()
    return fallback, ImageFont.load_default(size=size)
