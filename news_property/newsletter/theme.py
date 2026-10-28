"""Thème de la newsletter (config/theme.yaml) : chargement, validation, contrastes WCAG."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, UnidentifiedImageError

from news_property.config import PROJECT_DIR, ConfigError

THEME_FILE = PROJECT_DIR / "config" / "theme.yaml"
COLOR_KEYS = (
    "primary",
    "secondary",
    "text",
    "muted",
    "background",
    "surface",
    "border",
    "highlight",
    "highlight_text",
)
SECTION_KEYS = ("une", *(f"rubrique_{n}" for n in range(1, 7)))
FONT_KEYS = ("body", "headings", "labels")
MIN_CONTRAST = 4.5  # WCAG AA, texte courant
MIN_CONTRAST_LARGE = 3.0  # WCAG AA, grand texte (≥ 24 px, ou ≥ 19 px en gras)
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Pile de polices : noms de polices séparés par des virgules (lettres, chiffres, espaces,
# tirets, guillemets) ; rien qui permette d'injecter du CSS ou une police en ligne.
_FONT_STACK = re.compile(r"""^[A-Za-z0-9 ,'"\-]+$""")
GENERIC_FAMILIES = ("serif", "sans-serif", "monospace")


class ThemeError(ConfigError):
    """Thème invalide (message en français, nommant le champ fautif)."""


@dataclass(frozen=True)
class ContrastCheck:
    """Contrôle de contraste entre deux couleurs du thème (minimum : 4,5:1 pour le texte
    courant, 3:1 pour le grand texte)."""

    label: str
    foreground: str
    background: str
    ratio: float
    minimum: float = MIN_CONTRAST

    @property
    def ok(self) -> bool:
        """Vrai si le contraste atteint le minimum (WCAG AA)."""
        return self.ratio >= self.minimum

    @property
    def ratio_label(self) -> str:
        """Ratio arrondi, à la française (« 4,48:1 »)."""
        return _ratio_label(self.ratio)

    @property
    def minimum_label(self) -> str:
        """Minimum à la française (« 4,5:1 », « 3:1 »)."""
        return f"{self.minimum:g}".replace(".", ",") + ":1"


def _ratio_label(ratio: float) -> str:
    """« 4,48:1 »."""
    return f"{ratio:.2f}".replace(".", ",") + ":1"


@dataclass(frozen=True)
class SectionAccent:
    """Couleur d'accent d'une rubrique et couleur du numéro écrit dessus."""

    color: str
    on_color: str


@dataclass(frozen=True)
class Theme:
    """Thème validé."""

    name: str
    tagline: str
    colors: dict[str, str]
    section_colors: dict[str, str]
    font_body: str
    font_headings: str
    font_labels: str
    logo: Path
    logo_reverse: Path
    logo_alt: str  # texte alternatif des logos (lu par les lecteurs d'écran)
    footer_text: str
    section_titles: dict[str, str]
    source: Path

    def accent(self, key: str) -> SectionAccent:
        """Accent de la rubrique (une, rubrique_1…) : le numéro est écrit dans la couleur
        (fond blanc ou couleur secondaire « secondary ») qui contraste le mieux avec l'accent."""
        color = self.section_colors[key]
        on_color = max(
            (self.colors["background"], self.colors["secondary"]),
            key=lambda candidate: contrast_ratio(candidate, color),
        )
        return SectionAccent(color, on_color)

    def contrast_checks(self) -> list[ContrastCheck]:
        """Contrastes vérifiés, tels que les gabarits utilisent les couleurs.

        Texte courant (4,5:1) : texte et texte secondaire sur le fond et sur les encadrés,
        titres et liens (secondary) sur le fond, badge opportunité. Grand texte (3:1) : nom
        et mois écrits dans la couleur du fond sur le bandeau (primary), numéros des
        rubriques sur leur couleur d'accent. La couleur principale n'est jamais utilisée
        pour du petit texte."""
        c = self.colors
        checks = [
            ContrastCheck(label, fg, bg, contrast_ratio(fg, bg), minimum)
            for label, fg, bg, minimum in (
                ("Texte sur le fond", c["text"], c["background"], MIN_CONTRAST),
                ("Texte sur les encadrés", c["text"], c["surface"], MIN_CONTRAST),
                ("Texte secondaire sur le fond", c["muted"], c["background"], MIN_CONTRAST),
                ("Texte secondaire sur les encadrés", c["muted"], c["surface"], MIN_CONTRAST),
                ("Titres et liens sur le fond", c["secondary"], c["background"], MIN_CONTRAST),
                ("Badge « Transaction »", c["highlight_text"], c["highlight"], MIN_CONTRAST),
                (
                    "Grand texte du bandeau sur la couleur principale",
                    c["background"],
                    c["primary"],
                    MIN_CONTRAST_LARGE,
                ),
            )
        ]
        for key in SECTION_KEYS:
            accent = self.accent(key)
            label = "Une" if key == "une" else f"rubrique {key.removeprefix('rubrique_')}"
            checks.append(
                ContrastCheck(
                    f"Numéro sur la couleur d'accent ({label})",
                    accent.on_color,
                    accent.color,
                    contrast_ratio(accent.on_color, accent.color),
                    MIN_CONTRAST_LARGE,
                )
            )
        return checks

    def css_variables(self) -> str:
        """Déclarations CSS « --cp-... » du thème, pour un bloc :root { … }.

        Sans risque d'injection : couleurs (#RRGGBB) et polices (lettres, chiffres, espaces,
        virgules, tirets, guillemets) ont été validées au chargement."""
        lines = [f"--cp-{key.replace('_', '-')}: {value};" for key, value in self.colors.items()]
        lines += [
            f"--cp-font-body: {self.font_body};",
            f"--cp-font-headings: {self.font_headings};",
            f"--cp-font-labels: {self.font_labels};",
        ]
        return " ".join(lines)

    def warnings(self) -> list[str]:
        """Avertissements (non bloquants) pour les contrastes sous le minimum."""
        return [
            f"Contraste insuffisant - {check.label} : {check.ratio_label} "
            f"({check.foreground} sur {check.background}, minimum conseillé "
            f"{check.minimum_label})."
            for check in self.contrast_checks()
            if not check.ok
        ]


def _channel(value: int) -> float:
    """Composante sRGB linéarisée (formule WCAG 2.x)."""
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """Luminance relative d'une couleur #RRGGBB (WCAG)."""
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first: str, second: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs (de 1:1 à 21:1)."""
    light, dark = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _text(data: dict[str, Any], key: str, where: str, *, max_length: int = 200) -> str:
    """Texte obligatoire, non vide, de longueur raisonnable."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ThemeError(f"{where} : « {key} » est obligatoire (texte).")
    if len(value) > max_length:
        raise ThemeError(f"{where} : « {key} » est trop long ({max_length} caractères au plus).")
    return value.strip()


def _font_stack(value: Any, key: str, where: str) -> str:
    """Pile de polices système (ex. « Arial, Helvetica, sans-serif »)."""
    if not isinstance(value, str) or not value.strip() or not _FONT_STACK.match(value):
        raise ThemeError(
            f"{where} : « fonts.{key} » doit être une liste de polices système séparées par des "
            "virgules (ex. « Arial, Helvetica, sans-serif »), sans adresse ni police en ligne."
        )
    stack = ", ".join(part.strip() for part in value.split(",") if part.strip())
    if stack.split(", ")[-1].strip("'\"").lower() not in GENERIC_FAMILIES:
        raise ThemeError(
            f"{where} : « fonts.{key} » doit se terminer par une famille générique "
            "(sans-serif, serif ou monospace), utilisée si aucune police n'est installée."
        )
    return stack


def _color(data: dict[str, Any], key: str, block: str, where: str) -> str:
    """Couleur #RRGGBB obligatoire, renvoyée en majuscules."""
    value = data.get(key)
    if not isinstance(value, str) or not _HEX.match(value.strip()):
        raise ThemeError(
            f"{where} : la couleur « {block}.{key} » doit être au format #RRGGBB "
            f"(ex. #8D2562) ; valeur lue : {value!r}."
        )
    return value.strip().upper()


def _image(data: dict[str, Any], key: str, where: str, project_dir: Path) -> Path:
    """Fichier image PNG ou JPEG existant (chemin relatif au dossier du projet)."""
    value = _text(data, key, where, max_length=300)
    path = Path(value)
    path = path if path.is_absolute() else project_dir / path
    if not path.is_file():
        raise ThemeError(f"{where} : {key} introuvable : {value} (cherché dans {path}).")
    try:
        with Image.open(path) as image:
            if image.format not in ("PNG", "JPEG"):
                raise ThemeError(f"{where} : « {key} » doit être une image PNG ou JPEG.")
    except (UnidentifiedImageError, OSError) as exc:
        raise ThemeError(f"{where} : « {key} » ({value}) n'est pas une image lisible.") from exc
    return path


def load_theme(path: Path = THEME_FILE, project_dir: Path = PROJECT_DIR) -> Theme:
    """Lit et valide le thème. Lève ThemeError avec un message clair en cas de problème.

    Contrôle les couleurs (#RRGGBB), les polices (système uniquement), les logos (fichiers
    PNG ou JPEG existants) et les titres des rubriques. Les contrastes insuffisants ne sont pas des
    erreurs : voir Theme.warnings().
    """
    where = path.name
    if not path.is_file():
        raise ThemeError(f"Fichier de thème introuvable : {path}.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = f", ligne {mark.line + 1}" if mark is not None else ""
        raise ThemeError(
            f"{where}{line} : YAML illisible ({getattr(exc, 'problem', exc)})."
        ) from exc
    if not isinstance(data, dict):
        raise ThemeError(f"{where} : contenu invalide.")

    colors = data.get("colors")
    if not isinstance(colors, dict):
        raise ThemeError(f"{where} : le bloc « colors: » est absent ou invalide.")
    checked_colors: dict[str, str] = {}
    for key in COLOR_KEYS:
        checked_colors[key] = _color(colors, key, "colors", where)

    section_colors_data = data.get("section_colors")
    if not isinstance(section_colors_data, dict):
        raise ThemeError(f"{where} : le bloc « section_colors: » est absent ou invalide.")
    section_colors = {
        key: _color(section_colors_data, key, "section_colors", where) for key in SECTION_KEYS
    }

    fonts = data.get("fonts")
    if not isinstance(fonts, dict):
        raise ThemeError(f"{where} : le bloc « fonts: » est absent ou invalide.")
    font_stacks = {key: _font_stack(fonts.get(key), key, where) for key in FONT_KEYS}

    titles = data.get("section_titles")
    if not isinstance(titles, dict):
        raise ThemeError(f"{where} : le bloc « section_titles: » est absent ou invalide.")
    section_titles = {
        key: _text(titles, key, f"{where}, section_titles", max_length=80) for key in SECTION_KEYS
    }

    name = _text(data, "name", where, max_length=80)
    logo_alt = _text(data, "logo_alt", where, max_length=80) if data.get("logo_alt") else name
    return Theme(
        name=name,
        logo_alt=logo_alt,
        tagline=_text(data, "tagline", where),
        colors=checked_colors,
        section_colors=section_colors,
        font_body=font_stacks["body"],
        font_headings=font_stacks["headings"],
        font_labels=font_stacks["labels"],
        logo=_image(data, "logo", where, project_dir),
        logo_reverse=_image(data, "logo_reverse", where, project_dir),
        footer_text=_text(data, "footer_text", where, max_length=300),
        section_titles=section_titles,
        source=path,
    )
