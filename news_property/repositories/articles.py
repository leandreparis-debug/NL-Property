"""Dépôt des articles de veille (rubriques 1 à 3)."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Sequence

from news_property.db.connection import transaction
from news_property.models import ARTICLE_RUBRICS, Article, clip_evidence
from news_property.repositories import _common
from news_property.repositories._common import from_iso_datetime, to_iso_datetime, utcnow

_COLUMNS = (
    "id, newsletter_id, rubric, actor_id, actor_label, title, published_at, source_name, url,"
    " link_verified_by_user, excerpt, body_text, is_transaction, transaction_source,"
    " transaction_evidence, transaction_terms, surface_sqm, selected, position, collected_at"
)
_SCOPE = "newsletter_id = ? AND rubric = ?"


def _from_row(row: sqlite3.Row) -> Article:
    """Ligne SQL → Article."""
    return Article(
        id=row["id"],
        newsletter_id=row["newsletter_id"],
        rubric=row["rubric"],
        actor_id=row["actor_id"],
        actor_label=row["actor_label"],
        title=row["title"],
        published_at=from_iso_datetime(row["published_at"]),
        source_name=row["source_name"],
        url=row["url"],
        link_verified_by_user=bool(row["link_verified_by_user"]),
        excerpt=row["excerpt"],
        body_text=row["body_text"],
        is_transaction=bool(row["is_transaction"]),
        transaction_source=row["transaction_source"],
        transaction_evidence=row["transaction_evidence"],
        transaction_terms=json.loads(row["transaction_terms"]),
        surface_sqm=row["surface_sqm"],
        selected=bool(row["selected"]),
        position=row["position"],
        collected_at=from_iso_datetime(row["collected_at"]),
    )


def _check_rubric(rubric: int) -> None:
    """Vérifie le numéro de rubrique."""
    if rubric not in ARTICLE_RUBRICS:
        raise ValueError(f"Rubrique d'article invalide : {rubric!r} (1 ou 2).")


def get(conn: sqlite3.Connection, article_id: int) -> Article | None:
    """Article par identifiant, ou None."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM articles WHERE id = ?", (article_id,)).fetchone()
    return _from_row(row) if row else None


def add(conn: sqlite3.Connection, article: Article) -> Article | None:
    """Ajoute un article et le renvoie avec son identifiant.

    Renvoie None, sans erreur, si la même URL existe déjà pour cette newsletter
    (dédoublonnage). Si article.position est None, l'article est placé en fin de rubrique ;
    si collected_at est None, il prend la date courante. ValueError si l'URL n'est pas
    http(s) (contrôle fait à la construction de l'Article).
    """
    # Copie : revalide (URL, rubrique...) et évite les effets de bord.
    article = dataclasses.replace(article)
    with transaction(conn):
        if article.position is None:
            article.position = _common.next_position(
                conn, "articles", _SCOPE, (article.newsletter_id, article.rubric)
            )
        if article.collected_at is None:
            article.collected_at = utcnow()
        cursor = conn.execute(
            "INSERT INTO articles (newsletter_id, rubric, actor_id, actor_label, title,"
            " published_at, source_name, url, link_verified_by_user, excerpt, body_text,"
            " is_transaction, surface_sqm, selected, position, collected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (newsletter_id, url) DO NOTHING",
            (
                article.newsletter_id,
                article.rubric,
                article.actor_id,
                article.actor_label,
                article.title,
                to_iso_datetime(article.published_at),
                article.source_name,
                article.url,
                int(article.link_verified_by_user),
                article.excerpt,
                article.body_text,
                int(article.is_transaction),
                article.surface_sqm,
                int(article.selected),
                article.position,
                to_iso_datetime(article.collected_at),
            ),
        )
        if cursor.rowcount == 0:
            return None
        article.id = cursor.lastrowid
    return article


def list_for_rubric(
    conn: sqlite3.Connection, newsletter_id: int, rubric: int, selected_only: bool = False
) -> list[Article]:
    """Articles d'une rubrique dans l'ordre d'affichage (position) ; selected_only pour
    n'avoir que les articles retenus."""
    _check_rubric(rubric)
    sql = f"SELECT {_COLUMNS} FROM articles WHERE {_SCOPE}"
    if selected_only:
        sql += " AND selected = 1"
    rows = conn.execute(sql + " ORDER BY position, id", (newsletter_id, rubric))
    return [_from_row(row) for row in rows]


def list_for_newsletter(conn: sqlite3.Connection, newsletter_id: int) -> list[Article]:
    """Tous les articles d'une newsletter (rubrique 1 puis 2, dans l'ordre d'affichage)."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM articles WHERE newsletter_id = ? ORDER BY rubric, position, id",
        (newsletter_id,),
    )
    return [_from_row(row) for row in rows]


def update(conn: sqlite3.Connection, article: Article) -> Article:
    """Enregistre les modifications d'un article existant (modification par l'utilisateur).

    Le lien (url) n'est jamais modifié : il reste celui de l'article d'origine. Si le
    repère « transaction » change, il passe en origine « manual » : la détection
    automatique n'y touchera plus. La preuve de détection n'est pas modifiée.

    LookupError si l'article n'existe pas.
    """
    article_id = _common.require_id(article.id, "Article")
    article = dataclasses.replace(article)
    cursor = conn.execute(
        "UPDATE articles SET rubric = ?, actor_id = ?, actor_label = ?, title = ?,"
        " published_at = ?, source_name = ?, excerpt = ?,"
        " transaction_source = CASE WHEN is_transaction <> ? THEN 'manual'"
        " ELSE transaction_source END,"
        " is_transaction = ?, surface_sqm = ?, selected = ?,"
        " position = COALESCE(?, position), body_text = ? WHERE id = ?",
        (
            article.rubric,
            article.actor_id,
            article.actor_label,
            article.title,
            to_iso_datetime(article.published_at),
            article.source_name,
            article.excerpt,
            int(article.is_transaction),
            int(article.is_transaction),
            article.surface_sqm,
            int(article.selected),
            article.position,
            article.body_text,
            article_id,
        ),
    )
    if cursor.rowcount == 0:
        raise LookupError(f"Article introuvable (id {article_id}).")
    updated = get(conn, article_id)
    assert updated is not None
    return updated


def set_transaction(conn: sqlite3.Connection, article_id: int, is_transaction: bool) -> None:
    """Pose ou retire le repère « transaction » à la main (origine « manual » : la détection
    automatique ne modifiera plus cet article)."""
    conn.execute(
        "UPDATE articles SET is_transaction = ?, transaction_source = 'manual' WHERE id = ?",
        (int(is_transaction), article_id),
    )


def apply_auto_transaction(
    conn: sqlite3.Connection,
    article_id: int,
    is_transaction: bool,
    evidence: str | None,
    terms: Sequence[str],
    surface_sqm: int | None = None,
) -> bool:
    """Enregistre le résultat de la détection automatique (origine « auto »).

    Ne fait rien si le repère a été modifié à la main ; renvoie True si l'article a été
    mis à jour. La surface n'est posée que si elle est encore inconnue.
    """
    cursor = conn.execute(
        "UPDATE articles SET is_transaction = ?, transaction_source = 'auto',"
        " transaction_evidence = ?, transaction_terms = ?,"
        " surface_sqm = COALESCE(surface_sqm, ?)"
        " WHERE id = ? AND COALESCE(transaction_source, '') <> 'manual'",
        (
            int(is_transaction),
            clip_evidence(evidence) if is_transaction else None,
            json.dumps(list(terms) if is_transaction else [], ensure_ascii=False),
            surface_sqm,
            article_id,
        ),
    )
    return cursor.rowcount > 0


def move_to_rubric(
    conn: sqlite3.Connection,
    article_id: int,
    rubric: int,
    actor_id: int | None,
    actor_label: str | None,
) -> Article:
    """Déplace un article dans l'autre rubrique, en fin de liste, avec son acteur.

    Les autres champs (lien, sélection, texte) sont conservés. LookupError si absent.
    """
    _check_rubric(rubric)
    article = get(conn, article_id)
    if article is None:
        raise LookupError(f"Article introuvable (id {article_id}).")
    with transaction(conn):
        position = _common.next_position(conn, "articles", _SCOPE, (article.newsletter_id, rubric))
        conn.execute(
            "UPDATE articles SET rubric = ?, actor_id = ?, actor_label = ?, position = ?"
            " WHERE id = ?",
            (rubric, actor_id, actor_label, position, article_id),
        )
    moved = get(conn, article_id)
    assert moved is not None
    return moved


def set_selected(conn: sqlite3.Connection, article_id: int, selected: bool) -> None:
    """Retient (True) ou écarte (False) un article de la newsletter."""
    conn.execute("UPDATE articles SET selected = ? WHERE id = ?", (int(selected), article_id))


def reorder(
    conn: sqlite3.Connection, newsletter_id: int, rubric: int, ordered_ids: Sequence[int]
) -> None:
    """Fixe l'ordre d'affichage d'une rubrique (les articles non cités suivent)."""
    _check_rubric(rubric)
    _common.reorder(conn, "articles", _SCOPE, (newsletter_id, rubric), ordered_ids)


def delete(conn: sqlite3.Connection, article_id: int) -> None:
    """Supprime un article."""
    conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
