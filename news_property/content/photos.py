"""Traitement des photos de visite envoyées par l'utilisateur.

Chaque photo est vérifiée en l'ouvrant avec Pillow (jamais d'après son extension ou le
type annoncé par le navigateur), redressée selon son orientation EXIF, débarrassée de
toutes ses métadonnées (dont la localisation GPS), réduite à 1600 px sur le grand côté et
enregistrée en JPEG qualité 85 sous un nom généré par l'application.
"""

from __future__ import annotations

import io
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
MAX_SIDE = 1600
JPEG_QUALITY = 85
# Au-delà, l'image est refusée (protection contre les images piégées très lourdes).
MAX_PIXELS = 60_000_000


class PhotoError(ValueError):
    """Photo refusée ; le message (en français) est affiché à l'utilisateur."""


@dataclass(frozen=True)
class SavedPhoto:
    """Photo enregistrée : nom relatif au dossier photos, dimensions, poids."""

    file_name: str  # ex. « 2026-10/5f0c....jpg »
    width: int
    height: int
    size_bytes: int


def _flatten(image: Image.Image) -> Image.Image:
    """Convertit en RVB ; la transparence éventuelle est posée sur un fond blanc."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def process_photo(data: bytes, photos_dir: Path, month: str, original_name: str = "") -> SavedPhoto:
    """Vérifie, nettoie et enregistre une photo ; renvoie son nom relatif au dossier photos.

    Lève PhotoError (message en français) si le fichier n'est pas une image JPEG, PNG ou
    WebP lisible. original_name ne sert qu'aux messages d'erreur : il n'est jamais utilisé
    comme nom de fichier.
    """
    label = f"« {original_name} »" if original_name else "Le fichier"
    if not data:
        raise PhotoError(f"{label} est vide.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ACCEPTED_FORMATS:
                    raise PhotoError(
                        f"{label} n'est pas une photo JPEG, PNG ou WebP "
                        f"(format détecté : {source.format or 'inconnu'})."
                    )
                if source.width * source.height > MAX_PIXELS:
                    raise PhotoError(f"{label} est une image beaucoup trop grande.")
                source.load()
                image = ImageOps.exif_transpose(source)
                image = _flatten(image)
    except PhotoError:
        raise
    except UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning:
        raise PhotoError(
            f"{label} n'est pas une image lisible (formats acceptés : JPEG, PNG, WebP)."
        ) from None
    except OSError, SyntaxError, ValueError:
        raise PhotoError(f"{label} est une image abîmée ou incomplète.") from None

    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    # Nouvelle image sans aucune métadonnée (EXIF, GPS, commentaires, profils).
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    relative = f"{month}/{uuid.uuid4().hex}.jpg"
    target = photos_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    clean.save(target, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return SavedPhoto(
        file_name=relative,
        width=clean.width,
        height=clean.height,
        size_bytes=target.stat().st_size,
    )
