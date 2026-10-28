"""Jeu de démonstration pour essayer la relecture et les exports sans collecte réelle.

Usage : python -m news_property.tools.demo [--reset]

Crée la newsletter du mois fictif 2000-01 (et seulement celui-ci) avec des articles
clairement marqués « [DÉMO] », dont les liens pointent vers https://example.com/demo/...,
deux articles de réglementation, deux visites d'entrepôts avec des photos générées
(marquées « DÉMO »), trois rendez-vous
au calendrier (dont un avec un résumé), trois absences et deux liens vers des documents
utiles. Ces textes sont inventés pour la démonstration : ils ne décrivent aucun fait réel.
La détection (transactions, surfaces) est ensuite lancée sur ce mois.
"""

from __future__ import annotations

import argparse
import io
import random
import sqlite3
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import UTC, date, datetime

from PIL import Image, ImageDraw

from news_property.bootstrap import prepare_database
from news_property.config import ConfigError, Settings, load_settings
from news_property.content.photos import process_photo
from news_property.db import DatabaseError
from news_property.db.connection import get_connection
from news_property.detect.runner import run_detection
from news_property.fonts import system_font
from news_property.models import Absence, Article, DocumentLink, Event, Visit, VisitPhoto
from news_property.repositories import (
    absences,
    actors,
    articles,
    document_links,
    events,
    newsletters,
    visits,
)

DEMO_MONTH = "2000-01"
DEMO_SOURCE = "Source de démonstration"
DEMO_PREFIX = "[DÉMO] "
DEMO_DOCS_URL = "https://example.com/demo/documents"

# (rubrique, acteur suivi ou None, titre, texte de l'article)
DEMO_ARTICLES: tuple[tuple[int, str | None, str, str], ...] = (
    (
        1,
        None,
        "Le marché des entrepôts reste dynamique en Île-de-France",
        "Exemple fictif. La demande placée en entrepôts de plus de 5 000 m² progresse en "
        "Île-de-France, portée par la distribution et le e-commerce. Les loyers restent "
        "stables sur l'axe nord.",
    ),
    (
        1,
        None,
        "Un parc logistique de 60 000 m² sera livré près de Lyon",
        "Exemple fictif. Un promoteur régional a signé un bail en l'état futur d'achèvement "
        "pour une plateforme logistique de 60 000 m² à Démo-sur-Rhône. Livraison prévue "
        "en 2002.",
    ),
    (
        1,
        None,
        "Taux de vacance : les grandes cellules se font rares",
        "Exemple fictif. Le taux de vacance des entrepôts de classe A passe sous 4 % dans "
        "les principaux marchés de la dorsale.",
    ),
    (
        1,
        None,
        "Entrepôts XXL : les collectivités durcissent les règles",
        "Exemple fictif. Plusieurs intercommunalités conditionnent les permis de nouveaux "
        "entrepôts à des toitures photovoltaïques et à la désimperméabilisation des parkings.",
    ),
    (
        1,
        None,
        "La logistique urbaine cherche des messageries en première couronne",
        "Exemple fictif. Les transporteurs recherchent des messageries de 3 000 à 8 000 m² "
        "près des grandes agglomérations pour le dernier kilomètre.",
    ),
    (
        1,
        None,
        "<script>alert('démo')</script> Un titre piégé pour tester l'affichage",
        "Exemple fictif. Ce titre contient du code : il doit s'afficher comme du texte, sans "
        "jamais s'exécuter.",
    ),
    (
        2,
        "Prologis",
        "Prologis livre une plateforme de 40 000 m² à Démo-Ville",
        "Exemple fictif. Prologis a livré ce mois-ci une plateforme logistique de 40 000 m² "
        "à Démo-Ville, entièrement louée à un logisticien.",
    ),
    (
        2,
        "Geodis",
        "Geodis prend à bail 25 000 m² dans le Nord",
        "Exemple fictif. Le logisticien Geodis a signé un bail de neuf ans sur un entrepôt "
        "de 25 000 m² à Démo-les-Flandres.",
    ),
    (
        2,
        "Argan",
        "Argan présente ses résultats annuels",
        "Exemple fictif. La foncière Argan publie des revenus locatifs en hausse et confirme "
        "son plan de développement.",
    ),
    (
        2,
        "Carrefour",
        "Carrefour modernise sa logistique alimentaire",
        "Exemple fictif. Le distributeur annonce la rénovation de plusieurs entrepôts frais "
        "et l'automatisation d'un site en région parisienne.",
    ),
    (
        2,
        "CBRE",
        "CBRE publie son bilan du marché logistique",
        "Exemple fictif. Le conseil dresse le bilan de l'année : volumes investis, loyers et "
        "perspectives pour les entrepôts.",
    ),
    (
        3,
        None,
        "Un décret durcit les règles incendie des entrepôts ICPE",
        "Exemple fictif. Un décret publié ce mois-ci renforce les exigences de sécurité "
        "incendie des entrepôts soumis à la réglementation des installations classées, "
        "avec un délai de mise en conformité de deux ans.",
    ),
    (
        3,
        None,
        "Zéro artificialisation nette : ce qui change pour les projets logistiques",
        "Exemple fictif. La loi fixe un nouveau calendrier de réduction de l'artificialisation "
        "des sols ; les permis de construire des parcs logistiques devront justifier la "
        "sobriété foncière du projet.",
    ),
)

# (site, lieu, jour de visite, propriétaire, surface, description, couleurs des photos)
DEMO_VISITS: tuple[tuple[str, str, int, str, int, str, tuple[tuple[int, int, int], ...]], ...] = (
    (
        "Plateforme de Démo-sur-Rhône",
        "Démo-sur-Rhône (69)",
        12,
        "Carrefour Supply Chain",
        42000,
        "Exemple fictif : visite du site avec l'équipe exploitation.\n"
        "Points d'attention : toiture, sprinklage et quais.",
        ((141, 37, 98), (94, 23, 65)),
    ),
    (
        "Entrepôt de Démo-les-Flandres",
        "Démo-les-Flandres (59)",
        20,
        "Carrefour Property",
        58500,
        "Exemple fictif : entrepôt frais, un seul locataire.",
        ((178, 71, 138),),
    ),
)
# (titre, jour, jour de fin ou None, lieu, résumé)
DEMO_EVENTS: tuple[tuple[str, int, int | None, str, str], ...] = (
    (
        "Comité immobilier logistique",
        8,
        None,
        "Massy, siège",
        "Exemple fictif : revue des projets en cours et arbitrages du trimestre.\n"
        "Ordre du jour envoyé la semaine précédente.",
    ),
    ("Salon de démonstration de la logistique", 18, 20, "Paris, Porte de Versailles", ""),
    ("Visite de l'entrepôt de Démo-Ville", 27, None, "Démo-Ville (77)", ""),
)
# (personne, jour, jour de fin ou None, motif)
DEMO_ABSENCES: tuple[tuple[str, int, int | None, str], ...] = (
    ("Alice Exemple", 3, 7, "Congés"),
    ("Bruno Démo", 14, 14, "Formation"),
    ("Chloé Test", 24, 28, "Déplacement"),
)


def demo_photo(color: tuple[int, int, int], label: str, seed: int) -> bytes:
    """Photo de démonstration (JPEG 2400 × 1800) clairement marquée « DÉMO ».

    Dégradé, formes et léger grain, pour un poids comparable à une vraie photo.
    """
    rng = random.Random(seed)
    width, height = 2400, 1800
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(c * (0.55 + 0.45 * shade)) for c in color),
        )
    for _ in range(40):
        x, y = rng.randrange(width), rng.randrange(height)
        size = rng.randrange(60, 360)
        tint = tuple(min(255, c + rng.randrange(0, 90)) for c in color)
        draw.ellipse([x, y, x + size, y + size], fill=tint)
    noise = Image.effect_noise((width, height), 28).convert("RGB")
    image = Image.blend(image, noise, 0.12)
    draw = ImageDraw.Draw(image)
    title, font = system_font(240, "DÉMO", "DEMO")
    draw.rectangle([0, height // 2 - 220, width, height // 2 + 220], fill=(255, 255, 255))
    draw.text((width // 2, height // 2 - 60), title, fill=(141, 37, 98), font=font, anchor="mm")
    caption, small = system_font(90, label)
    draw.text((width // 2, height // 2 + 140), caption, fill=(30, 30, 30), font=small, anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _build_internal_content(conn: sqlite3.Connection, settings: Settings, nid: int) -> None:
    """Visites avec photos, calendrier, absences et documents utiles de démonstration."""
    seed = 0
    for site, location, day, owner, surface, description, colors in DEMO_VISITS:
        visit = visits.add(
            conn,
            Visit(
                newsletter_id=nid,
                site_name=DEMO_PREFIX + site,
                location=location,
                visit_date=date(2000, 1, day),
                owner_name=owner,
                surface_sqm=surface,
                description=description,
            ),
        )
        assert visit.id is not None
        for index, color in enumerate(colors, start=1):
            seed += 1
            data = demo_photo(color, f"{site} - photo {index}", seed)
            saved = process_photo(data, settings.photos_dir, DEMO_MONTH, "demo.jpg")
            visits.add_photo(
                conn,
                VisitPhoto(
                    visit_id=visit.id,
                    file_name=saved.file_name,
                    caption=f"[DÉMO] {site}, photo {index}",
                ),
            )
    for title, day, end_day, location, summary in DEMO_EVENTS:
        events.add(
            conn,
            Event(
                newsletter_id=nid,
                title=DEMO_PREFIX + title,
                event_date=date(2000, 1, day),
                end_date=date(2000, 1, end_day) if end_day else None,
                location=location,
                summary=summary or None,
            ),
        )
    for person, day, end_day, reason in DEMO_ABSENCES:
        absences.add(
            conn,
            Absence(
                newsletter_id=nid,
                person_name=person,
                start_date=date(2000, 1, day),
                end_date=date(2000, 1, end_day) if end_day else None,
                reason=reason,
            ),
        )
    document_links.replace_all(
        conn,
        nid,
        [
            DocumentLink(nid, 1, DEMO_DOCS_URL, label="[DÉMO] Tableau de suivi des sites"),
            DocumentLink(nid, 2, f"{DEMO_DOCS_URL}/trame", label="[DÉMO] Trame de compte rendu"),
        ],
    )


def build_demo(settings: Settings, *, reset: bool = False) -> int:
    """Crée la newsletter de démonstration 2000-01 ; renvoie le nombre d'articles créés.

    Lève ValueError si elle existe déjà et que reset est faux.
    """
    with closing(get_connection(settings)) as conn:
        existing = newsletters.get(conn, DEMO_MONTH)
        if existing is not None:
            if not reset:
                raise ValueError(
                    "La newsletter de démonstration (janvier 2000) existe déjà. "
                    "Relancez avec --reset pour la recréer."
                )
            assert existing.id is not None
            newsletters.delete(conn, existing.id, settings.photos_dir)
        newsletter = newsletters.get_or_create(conn, DEMO_MONTH)
        assert newsletter.id is not None
        created = 0
        for index, (rubric, actor_name, title, body) in enumerate(DEMO_ARTICLES, start=1):
            actor = actors.get_by_name(conn, actor_name) if actor_name else None
            article = Article(
                newsletter_id=newsletter.id,
                rubric=rubric,
                title=DEMO_PREFIX + title,
                source_name=DEMO_SOURCE,
                url=f"https://example.com/demo/{DEMO_MONTH}/article-{index}",
                actor_id=actor.id if actor else None,
                published_at=datetime(2000, 1, min(index * 2, 28), 9, 0, tzinfo=UTC),
                excerpt=body,
                body_text=body,
                collected_at=datetime(2000, 2, 1, 9, 0, tzinfo=UTC),
            )
            if articles.add(conn, article) is not None:
                created += 1
        run_detection(conn, DEMO_MONTH)
        _build_internal_content(conn, settings, newsletter.id)
    return created


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Crée le jeu de démonstration. Codes : 0 ; 2 si déjà présent ou configuration invalide."""
    parser = argparse.ArgumentParser(
        prog="python -m news_property.tools.demo",
        description="Crée une newsletter de démonstration (mois fictif 2000-01).",
    )
    parser.add_argument("--reset", action="store_true", help="recréer la démonstration")
    args = parser.parse_args(argv)
    try:
        settings = settings or load_settings()
        prepare_database(settings)
        created = build_demo(settings, reset=args.reset)
    except (ValueError, ConfigError, DatabaseError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out(f"Démonstration créée : {created} articles fictifs dans la newsletter de janvier 2000.")
    out("Ouvrez l'application, puis « Relire » → Janvier 2000. Les liens mènent à example.com.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
