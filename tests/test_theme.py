"""Tests du thème (config/theme.yaml), des formes de la charte et de l'écran « Thème »."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from news_property.newsletter import shapes
from news_property.newsletter.theme import ThemeError, contrast_ratio, load_theme
from tests.helpers import html_of, write_theme

SVG_NS = "{http://www.w3.org/2000/svg}"


def test_default_theme_loads() -> None:
    theme = load_theme()
    assert theme.colors["background"] == "#FFFFFF"
    assert theme.colors["primary"] == "#8D2562"  # violet du logo Carrefour Property
    assert re.fullmatch(r"#[0-9A-F]{6}", theme.colors["secondary"])
    assert theme.logo.is_file() and theme.logo_reverse.is_file()
    assert theme.logo_alt == "Carrefour Property"
    assert set(theme.section_colors) == set(theme.section_titles)
    assert set(theme.section_titles) == {"une", *(f"rubrique_{n}" for n in range(1, 8))}
    assert theme.font_body.endswith("sans-serif")
    assert theme.warnings() == []


def test_invalid_color_gives_clear_error(tmp_path: Path) -> None:
    path = write_theme(tmp_path, **{"colors.primary": "#12"})
    with pytest.raises(ThemeError, match=r"colors\.primary.*#RRGGBB.*'#12'"):
        load_theme(path, project_dir=tmp_path)


def test_missing_logo_gives_clear_error(tmp_path: Path) -> None:
    path = write_theme(tmp_path, logo="assets/absent.png")
    with pytest.raises(ThemeError, match="logo introuvable : assets/absent.png"):
        load_theme(path, project_dir=tmp_path)


def test_unsafe_font_is_refused(tmp_path: Path) -> None:
    path = write_theme(tmp_path, **{"fonts.body": "Arial; } body { color: red"})
    with pytest.raises(ThemeError, match="fonts.body"):
        load_theme(path, project_dir=tmp_path)


def test_yaml_error_names_the_line(tmp_path: Path) -> None:
    path = tmp_path / "theme.yaml"
    path.write_text("name: News Property\ncolors:\n  primary: [\n", encoding="utf-8")
    with pytest.raises(ThemeError, match=r"theme\.yaml, ligne \d+"):
        load_theme(path, project_dir=tmp_path)


def test_low_contrast_is_a_warning_not_an_error(tmp_path: Path) -> None:
    path = write_theme(tmp_path, **{"colors.muted": "#777777"})
    theme = load_theme(path, project_dir=tmp_path)  # non bloquant
    assert round(contrast_ratio("#777777", "#FFFFFF"), 2) == 4.48
    warnings = theme.warnings()
    assert any("Texte secondaire sur le fond" in w and "4,48:1" in w for w in warnings)
    assert all("minimum conseillé 4,5:1" in w for w in warnings)


def test_large_text_uses_the_3_to_1_minimum(tmp_path: Path) -> None:
    # Blanc sur le violet du logo : 8,2:1, bien au-dessus des 3:1 du grand texte.
    assert round(contrast_ratio("#FFFFFF", "#8D2562"), 1) == 8.2
    path = write_theme(tmp_path, **{"colors.primary": "#E0B3CC"})
    warnings = load_theme(path, project_dir=tmp_path).warnings()
    assert any("bandeau" in w and "minimum conseillé 3:1" in w for w in warnings)


def test_section_number_color_is_the_most_readable() -> None:
    theme = load_theme()
    assert theme.accent("rubrique_1").on_color == "#FFFFFF"  # sur violet foncé
    assert theme.accent("rubrique_3").on_color == "#5E1741"  # sur ocre doré (clair)
    assert all(check.ok for check in theme.contrast_checks())


def test_invalid_section_color_and_missing_font(tmp_path: Path) -> None:
    path = write_theme(tmp_path, **{"section_colors.rubrique_2": "violet"})
    with pytest.raises(ThemeError, match=r"section_colors\.rubrique_2"):
        load_theme(path, project_dir=tmp_path)
    path = write_theme(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"\n  labels: .*", "", text), encoding="utf-8")
    with pytest.raises(ThemeError, match="fonts.labels"):
        load_theme(path, project_dir=tmp_path)


def test_theme_css_variables_come_from_theme() -> None:
    css = load_theme().css_variables()
    assert "--cp-primary: #8D2562;" in css
    assert "--cp-highlight-text: #7A3A12;" in css
    assert "--cp-font-body: 'Century Gothic'," in css
    assert "--cp-font-labels: 'Bahnschrift Condensed'," in css


@pytest.mark.parametrize("kind", shapes.SHAPES)
def test_shapes_are_valid_svg(kind: str) -> None:
    root = ET.fromstring(shapes.shape_svg(kind, "#8D2562"))
    assert root.tag == f"{SVG_NS}svg" and root.get("aria-hidden") == "true"
    assert root.find(f"{SVG_NS}path").get("fill") == "#8D2562"  # type: ignore[union-attr]


def test_motif_is_valid_svg_with_diagonal_edge() -> None:
    svg = shapes.motif_svg("#FFFFFF", columns=4, rows=4)
    root = ET.fromstring(svg)
    path = root.find(f"{SVG_NS}path")
    assert path is not None
    # Colonnes de 1, 2, 3 puis 4 formes (bord en diagonale) : 10 formes, dont 3 nœuds
    # dessinés chacun en deux sous-chemins.
    assert path.get("d", "").count("M") == 10 + 3
    with pytest.raises(ValueError):
        shapes.shape_path("cercle", 0, 0, 10)


def test_shape_png_is_transparent() -> None:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(shapes.shape_png("arch", "#8D2562", 40))) as image:
        assert image.format == "PNG" and image.size == (40, 40) and image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0  # angle : transparent
        assert image.getpixel((20, 30))[:3] == (0x8D, 0x25, 0x62)  # centre bas : plein


# --------------------------------------------------------------------------- écran « Thème »


def test_theme_screen(client: FlaskClient) -> None:
    html = html_of(client.get("/theme"))
    assert "#8D2562" in html and "Century Gothic" in html and "Bahnschrift" in html
    assert "minimum" in html.lower()
    assert client.get("/theme/logo/blanc").status_code == 200
    assert client.get("/theme/logo/autre").status_code == 404
    assert "Tous les contrastes vérifiés" in html
    assert "theme.yaml" in html
    assert client.get("/theme/logo").status_code == 200
    assert 'href="/theme"' in html_of(client.get("/"))


def test_theme_screen_shows_warning_and_errors(
    app: Flask, client: FlaskClient, tmp_path: Path
) -> None:
    app.config["CP_THEME_FILE"] = write_theme(tmp_path, **{"colors.muted": "#777777"})
    html = html_of(client.get("/theme"))
    assert "Contraste insuffisant" in html and "4,48:1" in html and "Insuffisant" in html

    app.config["CP_THEME_FILE"] = write_theme(tmp_path, logo="absent.png")
    response = client.get("/theme")
    assert response.status_code == 200
    assert "logo introuvable" in html_of(response)
    assert client.get("/theme/logo").status_code == 404
    # Le reste de l'application reste utilisable avec un thème invalide.
    assert client.get("/").status_code == 200


def test_system_font_falls_back_without_accents(monkeypatch: pytest.MonkeyPatch) -> None:
    from news_property import fonts

    monkeypatch.setattr(fonts, "FONT_CANDIDATES", ("police-inexistante.ttf",))
    text, _ = fonts.system_font(20, "DÉMO")
    assert text == "DMO"
