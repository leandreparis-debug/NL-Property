"""E-mail HTML de la newsletter pour Gmail, et paquet lu par le script Apps Script.

Gmail impose ses règles : pas de SVG, pas de mise en page flex ou grid, styles écrits dans
chaque balise (« en ligne »), largeur fixe d'environ 600 px, et un message tronqué au-delà
de 102 Ko de HTML. L'e-mail est donc une mise en page en tableaux, avec les mêmes gabarits
partiels que l'aperçu (medium="email"). Les images (logos, motif, formes, photos) sont
jointes au message et appelées par « cid: » : elles s'affichent sans être hébergées
nulle part.

Le paquet (un fichier JSON : message MIME complet, PDF à imprimer, HTML, version texte,
images en base64) est déposé dans Google Drive ; le script apps_script/brouillon_gmail.gs
le lit et crée le brouillon Gmail. Rien n'est envoyé : l'humain envoie.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from flask import render_template
from markupsafe import Markup
from PIL import Image, ImageOps

from news_property.config import Settings
from news_property.newsletter.pdf import PdfError
from news_property.newsletter.shapes import SHAPES, motif_png, shape_png
from news_property.newsletter.theme import SECTION_KEYS, Theme, load_theme
from news_property.newsletter.view import (
    TRANSACTION_LABEL,
    AbsenceItem,
    ArticleItem,
    DocumentItem,
    EventItem,
    NewsletterView,
    VisitItem,
    build_newsletter_view,
)

EMAIL_WIDTH = 600
GMAIL_CLIP_BYTES = 102 * 1024  # au-delà, Gmail tronque le message (« Message tronqué »)
GMAIL_CLIP_MARGIN = 92 * 1024  # avertissement avant la limite
PACKAGE_FORMAT = "news-property-email/3"
# Adresse provisoire du lien « voir la version à imprimer » : le script Apps Script la
# remplace par le lien du PDF déposé dans Google Drive, avant de créer le brouillon.
PRINT_LINK_TOKEN = "CP-LIEN-PDF-A-REMPLACER"
MAX_LINE_BYTES = 900  # norme des e-mails : 998 octets au plus par ligne
MAIN_PHOTO_SIZE = (520, 480)  # affichée en 260 × 240 (écrans haute densité)
THUMB_SIZE = (184, 140)  # affichée en 92 × 70
JPEG_QUALITY = 80


@dataclass(frozen=True)
class EmailImage:
    """Image jointe au message, appelée dans le HTML par « cid:<cid> »."""

    cid: str
    file_name: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class EmailExport:
    """E-mail prêt à envoyer : objet, HTML (images en cid:), version texte, images."""

    month: str
    subject: str
    preheader: str
    html: str  # version envoyée (images en cid:)
    browser_html: str  # même e-mail, images en fichiers voisins (ouvrable dans un navigateur)
    text: str
    images: tuple[EmailImage, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    print_pdf: bytes | None = None  # PDF ouvert par le lien « voir la version à imprimer »

    @property
    def print_pdf_name(self) -> str:
        """Nom du PDF à imprimer (news-property-AAAA-MM.pdf)."""
        return f"news-property-{self.month}.pdf"

    @property
    def html_size(self) -> int:
        """Taille du HTML envoyé, en octets (UTF-8) : c'est elle que Gmail limite."""
        return len(self.html.encode("utf-8"))

    @property
    def images_size(self) -> int:
        """Poids total des images jointes, en octets."""
        return sum(len(image.data) for image in self.images)


@dataclass(frozen=True)
class ExportFiles:
    """Fichiers écrits par write_email_export."""

    folder: Path
    html: Path
    package: Path
    drive_copy: Path | None


def size_label(size: int) -> str:
    """Taille lisible en français (« 48,2 Ko », « 1,3 Mo »)."""
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} Ko".replace(".", ",")
    return f"{size / 1024 / 1024:.1f} Mo".replace(".", ",")


# --------------------------------------------------------------------------- styles


def email_styles(theme: Theme) -> dict[str, Markup]:
    """Styles en ligne de l'e-mail, construits depuis le thème validé (couleurs #RRGGBB,
    polices système) : aucune valeur extérieure n'y entre."""
    c = theme.colors
    body, headings, labels = theme.font_body, theme.font_headings, theme.font_labels
    styles = {
        "body": f"margin:0;padding:0;background:{c['border']};",
        "outer": f"background:{c['border']};padding:16px 8px;",
        "card": f"width:{EMAIL_WIDTH}px;max-width:{EMAIL_WIDTH}px;background:{c['background']};",
        "band": f"background:{c['primary']};padding:0;",
        "band_text": "padding:34px 0 30px 32px;",
        "title": (
            f"margin:0 0 6px;font-family:{headings};font-size:40px;line-height:1.1;"
            f"font-weight:700;color:{c['background']};"
        ),
        "month": (
            f"margin:0;font-family:{labels};font-size:24px;line-height:1.2;font-weight:700;"
            f"letter-spacing:1px;text-transform:uppercase;color:{c['background']};"
        ),
        "tagline": (
            f"background:{c['surface']};padding:12px 32px;font-family:{body};font-size:14px;"
            f"line-height:1.4;color:{c['text']};"
        ),
        "block": "padding:26px 32px 4px;",
        "une_title": (
            f"margin:0;font-family:{headings};font-size:20px;line-height:1.3;font-weight:700;"
            f"letter-spacing:1px;text-transform:uppercase;color:{c['secondary']};"
        ),
        "tile": (
            f"background:{c['surface']};border-radius:14px;padding:16px 16px 14px;"
            "vertical-align:top;"
        ),
        "figure_value": (
            f"margin:8px 0 0;font-family:{labels};font-size:44px;line-height:1;font-weight:700;"
            f"color:{c['secondary']};"
        ),
        "figure_label": (
            f"margin:4px 0 0;font-family:{labels};font-size:14px;line-height:1.3;"
            f"letter-spacing:0.5px;text-transform:uppercase;color:{c['text']};"
        ),
        "toc": f"border:1px solid {c['border']};border-radius:14px;padding:16px 20px;",
        "toc_title": (
            f"margin:0 0 10px;font-family:{labels};font-size:15px;font-weight:700;"
            f"letter-spacing:1px;text-transform:uppercase;color:{c['secondary']};"
        ),
        "toc_cell": "padding:4px 8px 4px 0;vertical-align:middle;",
        "toc_number": (
            f"font-family:{labels};font-size:15px;font-weight:700;color:{c['secondary']};"
        ),
        "toc_title_item": (
            f"margin:0;font-family:{body};font-size:15px;line-height:1.3;font-weight:700;"
            f"color:{c['secondary']};"
        ),
        "toc_count": f"margin:0;font-family:{labels};font-size:13px;color:{c['muted']};",
        "number": (
            f"width:52px;height:52px;padding:0 0 6px;border-radius:26px 26px 0 0;"
            f"font-family:{labels};font-size:26px;line-height:1;font-weight:700;"
            "text-align:center;vertical-align:bottom;"
        ),
        "section_title": (
            f"margin:0;font-family:{headings};font-size:21px;line-height:1.25;font-weight:700;"
            f"letter-spacing:1px;text-transform:uppercase;color:{c['secondary']};"
        ),
        "item": f"padding:14px 0;border-bottom:1px solid {c['border']};",
        "kicker": (
            f"margin:0 0 2px;font-family:{labels};font-size:13px;font-weight:700;"
            f"letter-spacing:0.5px;text-transform:uppercase;color:{c['muted']};"
        ),
        "item_title": (
            f"margin:0 0 6px;font-family:{headings};font-size:18px;line-height:1.3;"
            f"font-weight:700;color:{c['secondary']};"
        ),
        "link": f"color:{c['secondary']};text-decoration:none;",
        "meta": (
            f"margin:0 0 6px;font-family:{labels};font-size:13px;line-height:1.4;"
            f"letter-spacing:0.3px;color:{c['muted']};"
        ),
        "text": (
            f"margin:0 0 6px;font-family:{body};font-size:15px;line-height:1.5;color:{c['text']};"
        ),
        "badge": (
            f"display:inline-block;padding:2px 10px;border-radius:999px;"
            f"background:{c['highlight']};color:{c['highlight_text']};font-family:{labels};"
            "font-size:12px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;"
        ),
        "more": (
            f"font-family:{body};font-size:14px;font-weight:700;color:{c['secondary']};"
            "text-decoration:underline;"
        ),
        "photo": ("display:block;width:260px;height:240px;border:0;border-radius:130px 130px 0 0;"),
        "caption": (
            f"margin:4px 0 0;font-family:{body};font-size:12px;line-height:1.3;color:{c['muted']};"
        ),
        "thumb": "display:inline-block;width:92px;height:70px;border:0;border-radius:8px;",
        "card_cell": (
            f"background:{c['surface']};border-radius:0 10px 10px 0;padding:12px 14px;"
            "vertical-align:top;"
        ),
        "absence_cell": "padding:8px 0;vertical-align:top;",
        "strong": (
            f"margin:0 0 2px;font-family:{headings};font-size:16px;line-height:1.3;"
            f"font-weight:700;color:{c['secondary']};"
        ),
        "documents": f"background:{c['surface']};border-radius:14px;padding:18px 20px;",
        "button_cell": f"background:{c['secondary']};border-radius:999px;padding:10px 18px;",
        "button": (
            f"font-family:{body};font-size:15px;font-weight:700;color:{c['background']};"
            "text-decoration:none;"
        ),
        "footer": (
            f"padding:22px 32px 26px;border-top:1px solid {c['border']};text-align:center;"
            f"font-family:{body};font-size:13px;line-height:1.5;color:{c['muted']};"
        ),
        "footer_text": f"margin:0;font-family:{body};font-size:13px;color:{c['muted']};",
        "rule": "font-size:0;line-height:0;",
    }
    return {key: Markup(value) for key, value in styles.items()}


# --------------------------------------------------------------------------- images


def _png_resized(path: Path, width: int) -> bytes:
    """Image (logo) redimensionnée à width pixels de large, en PNG (transparence gardée)."""
    with Image.open(path) as source:
        image = source.convert("RGBA")
    height = round(image.height * width / image.width)
    buffer = io.BytesIO()
    image.resize((width, height), Image.Resampling.LANCZOS).save(
        buffer, format="PNG", optimize=True
    )
    return buffer.getvalue()


def _jpeg_cropped(path: Path, size: tuple[int, int]) -> bytes:
    """Photo recadrée au centre et réduite à size, en JPEG (Gmail ignore object-fit)."""
    with Image.open(path) as source:
        image = ImageOps.fit(source.convert("RGB"), size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue()


class _Images:
    """Collecte des images jointes : chaque image reçoit un cid unique et stable."""

    def __init__(self) -> None:
        self.items: dict[str, EmailImage] = {}

    def add(self, cid: str, data: bytes, extension: str) -> str:
        mime = "image/png" if extension == "png" else "image/jpeg"
        self.items[cid] = EmailImage(cid, f"{cid}.{extension}", mime, data)
        return cid


# --------------------------------------------------------------------------- texte


def _text_version(view: NewsletterView, theme: Theme, print_href: str | None = None) -> str:
    """Version texte de l'e-mail (lecteurs sans HTML, aperçus de notification)."""
    lines = [f"{theme.name.upper()} - {view.month_label}", theme.tagline, ""]
    if print_href:
        lines += [f"Voir la version à imprimer (PDF) : {print_href}", ""]
    lines.append(view.front_title.upper())
    lines.append(" · ".join(f"{f.value} {f.label}" for f in view.front))
    for section in view.visible_sections:
        lines += ["", f"{section.number_label}. {section.title.upper()}"]
        for item in section.items:
            if isinstance(item, ArticleItem):
                lines.append(f"- {item.title}")
                meta = " · ".join(
                    part for part in (item.source, item.date_label, item.surface_label) if part
                )
                if item.transaction:
                    meta += f" · {TRANSACTION_LABEL}"
                lines += [f"  {meta}", f"  {item.url}"]
            elif isinstance(item, VisitItem):
                details = " · ".join(part for part in (item.owner, item.surface_label) if part)
                when = f" - visite du {item.date_label}" if item.date_label else ""
                lines.append(f"- {item.site_name}, {item.location}{when}")
                if details:
                    lines.append(f"  {details}")
                lines += [f"  {line}" for line in item.description_lines if line]
            elif isinstance(item, EventItem):
                when = f" ({item.date_label})" if item.date_label else ""
                where = f" - {item.location}" if item.location else ""
                lines.append(f"- {item.title}{when}{where}")
                lines += [f"  {line}" for line in item.summary_lines if line]
            elif isinstance(item, AbsenceItem):
                reason = f" - {item.reason}" if item.reason else ""
                lines.append(f"- {item.person} : {item.period_label.lower()}{reason}")
            elif isinstance(item, DocumentItem):
                lines.append(f"- {item.label} : {item.url}")
    lines += ["", theme.footer_text]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- e-mail


def build_email(
    conn: sqlite3.Connection,
    month: str,
    settings: Settings,
    theme: Theme | None = None,
    make_print_pdf: Callable[[NewsletterView, Theme], bytes] | None = None,
) -> EmailExport:
    """Prépare l'e-mail du mois AAAA-MM (doit être appelé dans un contexte Flask, pour les
    gabarits). Seuls les articles retenus y figurent, comme dans l'aperçu.

    make_print_pdf fabrique la version PDF à imprimer : l'e-mail porte alors un lien
    « voir la version à imprimer », sous le bandeau et en pied de page (lien provisoire
    PRINT_LINK_TOKEN, remplacé par le script Apps Script par le lien du PDF dans Google
    Drive). Si la fabrication échoue (PdfError), l'e-mail est préparé sans ce lien, avec
    un avertissement.

    Lève NewsletterNotFoundError, EmptyNewsletterError ou ThemeError (messages en français).
    """
    theme = theme or load_theme()
    view = build_newsletter_view(conn, month, settings, theme)
    warnings: list[str] = []
    print_pdf: bytes | None = None
    if make_print_pdf is not None:
        try:
            print_pdf = make_print_pdf(view, theme)
        except PdfError as exc:
            warnings.append(f"E-mail préparé sans lien vers la version à imprimer : {exc}")
    images = _Images()
    accents = {key: theme.accent(key) for key in SECTION_KEYS}

    logo = images.add("cp-logo", _png_resized(theme.logo, 240), "png")
    logo_reverse = images.add("cp-logo-blanc", _png_resized(theme.logo_reverse, 340), "png")
    motif = images.add("cp-motif", motif_png(theme.colors["background"], opacity=0.9), "png")
    shape_of = {key: SHAPES[index % len(SHAPES)] for index, key in enumerate(SECTION_KEYS)}
    # Formes du sommaire et de la Une (visites, rendez-vous, actualités).
    used_icons = {entry.key for entry in view.toc} | {"rubrique_4", "rubrique_5", "rubrique_1"}
    icons = {
        key: images.add(
            f"cp-icone-{key.replace('_', '-')}", shape_png(shape_of[key], accents[key].color), "png"
        )
        for key in SECTION_KEYS
        if key in used_icons
    }
    photos: dict[str, str] = {}
    for section in view.visible_sections:
        for item in section.items:
            if not isinstance(item, VisitItem):
                continue
            if item.main_photo:
                name = item.main_photo.file_name
                photos[name] = images.add(
                    f"cp-photo-{len(photos) + 1}",
                    _jpeg_cropped(settings.photos_dir / name, MAIN_PHOTO_SIZE),
                    "jpg",
                )
            for photo in item.other_photos:
                photos[photo.file_name] = images.add(
                    f"cp-photo-{len(photos) + 1}",
                    _jpeg_cropped(settings.photos_dir / photo.file_name, THUMB_SIZE),
                    "jpg",
                )

    subject = f"{theme.name} - {view.month_label}"
    preheader = f"{view.month_label} : " + ", ".join(f"{f.value} {f.label}" for f in view.front)
    context = {
        "view": view,
        "theme": theme,
        "medium": "email",
        "st": email_styles(theme),
        "accents": accents,
        "subject": subject,
        "preheader": preheader,
        "transaction_label": TRANSACTION_LABEL,
        "show_other_photos": True,
        "width": EMAIL_WIDTH,
    }

    def render(src: dict[str, str], print_href: str | None) -> str:
        def to(cid: str) -> str:
            return src[cid]

        return render_template(
            "newsletter/email.html",
            **context,
            print_href=print_href,
            logo_src=to(logo),
            logo_reverse_src=to(logo_reverse),
            motif_src=to(motif),
            icon_src={key: to(cid) for key, cid in icons.items()},
            front_icon_src=tuple(to(icons[f"rubrique_{n}"]) for n in (4, 5, 1)),
            photo_src={name: to(cid) for name, cid in photos.items()},
        )

    token = PRINT_LINK_TOKEN if print_pdf else None
    html = render({cid: f"cid:{cid}" for cid in images.items}, token)
    browser_html = render(
        {cid: f"images/{image.file_name}" for cid, image in images.items.items()},
        f"news-property-{view.month}.pdf" if print_pdf else None,
    )
    export = EmailExport(
        month=view.month,
        subject=subject,
        preheader=preheader,
        html=html,
        browser_html=browser_html,
        text=_text_version(view, theme, token),
        images=tuple(images.items.values()),
        print_pdf=print_pdf,
    )
    if export.html_size > GMAIL_CLIP_MARGIN:
        warnings.append(
            f"Le HTML pèse {size_label(export.html_size)} : Gmail tronque les messages au-delà "
            "de 102 Ko (« Message tronqué »). Retirez quelques articles."
        )
    return replace(export, warnings=tuple(warnings))


def _short_lines(text: str) -> str:
    """Texte dont aucune ligne ne dépasse MAX_LINE_BYTES octets (norme des e-mails) :
    les lignes trop longues sont coupées entre deux balises, sinon sur une espace."""
    lines: list[str] = []
    for line in text.split("\n"):
        while len(line.encode("utf-8")) > MAX_LINE_BYTES:
            head = line[:MAX_LINE_BYTES]
            cut = max(head.rfind("><") + 1, head.rfind(" "))
            if cut <= 0:
                break  # rien à couper proprement (cas théorique) : la ligne reste longue
            lines.append(line[:cut])
            line = line[cut:].lstrip(" ")
        lines.append(line)
    return "\n".join(lines)


def mime_message(export: EmailExport) -> bytes:
    """Message complet au format MIME, tel que Gmail le stocke :

    multipart/alternative (version texte, puis HTML) ; le HTML est un multipart/related qui
    contient les images, chacune avec son Content-ID (appelé par « cid: » dans le HTML) et
    marquée « inline ». C'est cette forme que Gmail affiche dans un brouillon : les images
    jointes par GmailApp.createDraft(... inlineImages ...) n'y apparaissent pas toujours.
    Pas d'expéditeur ni de destinataire : Gmail met votre adresse, vous ajoutez les
    destinataires dans le brouillon.

    Texte et HTML sont en « 8bit » (UTF-8 lisible tel quel, lignes courtes) : le script Apps
    Script peut ainsi y remplacer PRINT_LINK_TOKEN par le lien du PDF.
    """
    message = EmailMessage(policy=SMTP)
    message["Subject"] = export.subject
    message.set_content(_short_lines(export.text), subtype="plain", charset="utf-8", cte="8bit")
    message.add_alternative(_short_lines(export.html), subtype="html", charset="utf-8", cte="8bit")
    html_part = message.get_payload()[1]
    for image in export.images:
        maintype, subtype = image.mime_type.split("/")
        html_part.add_related(
            image.data,
            maintype=maintype,
            subtype=subtype,
            cid=f"<{image.cid}>",
            filename=image.file_name,
            disposition="inline",
        )
    return message.as_bytes()


def package_json(export: EmailExport, created_at: datetime | None = None) -> str:
    """Paquet lu par le script Apps Script : message MIME complet (« raw », base64 URL,
    comme l'attend l'API Gmail), PDF à imprimer et adresse provisoire de son lien (s'il y en
    a un), et pour information l'objet, le HTML, le texte et les images."""
    created_at = created_at or datetime.now(UTC)
    payload = {
        "format": PACKAGE_FORMAT,
        "month": export.month,
        "createdAt": created_at.isoformat(timespec="seconds"),
        "raw": base64.urlsafe_b64encode(mime_message(export)).decode("ascii"),
        "printLinkToken": PRINT_LINK_TOKEN if export.print_pdf else None,
        "printPdf": (
            {
                "fileName": export.print_pdf_name,
                "mimeType": "application/pdf",
                "data": base64.b64encode(export.print_pdf).decode("ascii"),
            }
            if export.print_pdf
            else None
        ),
        "subject": export.subject,
        "htmlBody": export.html,
        "textBody": export.text,
        "images": [
            {
                "cid": image.cid,
                "fileName": image.file_name,
                "mimeType": image.mime_type,
                "data": base64.b64encode(image.data).decode("ascii"),
            }
            for image in export.images
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def export_folder(settings: Settings, month: str) -> Path:
    """Dossier de l'export e-mail du mois : <données>/exports/<AAAA-MM>/email."""
    return settings.exports_dir / month / "email"


def package_name(month: str) -> str:
    """Nom du paquet, reconnu par le script Apps Script (news-property-AAAA-MM.json)."""
    return f"news-property-{month}.json"


def write_email_export(export: EmailExport, settings: Settings) -> ExportFiles:
    """Écrit l'export (le précédent du même mois est remplacé) :

    - news-property-AAAA-MM.html et images/ : l'e-mail à ouvrir dans un navigateur ;
    - news-property-AAAA-MM.eml : le message complet (s'ouvre avec Outlook ou Thunderbird ;
      le lien « voir la version à imprimer » n'y a pas encore son adresse, posée par le
      script Apps Script) ;
    - news-property-AAAA-MM.pdf : la version à imprimer, s'il y en a une ;
    - news-property-AAAA-MM.json : le paquet pour le script Apps Script, copié aussi dans
      le dossier Google Drive (CP_DRIVE_DIR) s'il est configuré.
    """
    folder = export_folder(settings, export.month)
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "images").mkdir(parents=True)
    for image in export.images:
        (folder / "images" / image.file_name).write_bytes(image.data)
    html_path = folder / f"news-property-{export.month}.html"
    html_path.write_text(export.browser_html, encoding="utf-8")
    (folder / f"news-property-{export.month}.eml").write_bytes(mime_message(export))
    if export.print_pdf:
        (folder / export.print_pdf_name).write_bytes(export.print_pdf)
    package = folder / package_name(export.month)
    package.write_text(package_json(export), encoding="utf-8")
    drive_copy = None
    if settings.drive_dir is not None:
        drive_copy = settings.drive_dir / package.name
        shutil.copyfile(package, drive_copy)
    return ExportFiles(folder, html_path, package, drive_copy)
