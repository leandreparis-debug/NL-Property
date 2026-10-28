"""Tests des dépôts (newsletters, articles, collectes)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from news_property.models import Article, Newsletter
from news_property.repositories import actors, articles, collection_runs, newsletters


def _article(newsletter: Newsletter, url: str, title: str = "Titre", rubric: int = 1) -> Article:
    assert newsletter.id is not None
    return Article(
        newsletter_id=newsletter.id,
        rubric=rubric,
        title=title,
        source_name="Business Immo",
        url=url,
        published_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
        excerpt="Premières lignes de l'article.",
    )


# --------------------------------------------------------------------------- newsletters


def test_get_or_create_is_idempotent(conn: sqlite3.Connection) -> None:
    first = newsletters.get_or_create(conn, "2026-10")
    second = newsletters.get_or_create(conn, "2026-10")
    assert first == second
    assert first.id is not None and first.status == "draft"
    assert first.created_at is not None
    assert len(newsletters.list_all(conn)) == 1
    assert newsletters.get_by_id(conn, first.id) == first


@pytest.mark.parametrize("month", ["2026-13", "octobre", "2026-1", "2026-00", "", "26-10"])
def test_invalid_month(conn: sqlite3.Connection, month: str) -> None:
    with pytest.raises(ValueError, match="AAAA-MM"):
        newsletters.get_or_create(conn, month)


def test_list_all_most_recent_first(conn: sqlite3.Connection) -> None:
    for month in ("2026-09", "2026-11", "2025-12"):
        newsletters.get_or_create(conn, month)
    assert [n.month for n in newsletters.list_all(conn)] == ["2026-11", "2026-09", "2025-12"]
    assert newsletters.get(conn, "2026-01") is None


def test_status(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    updated = newsletters.set_status(conn, newsletter.id, "reviewed")
    assert updated.status == "reviewed"
    assert updated.updated_at is not None
    with pytest.raises(ValueError):
        newsletters.set_status(conn, newsletter.id, "envoyée")
    with pytest.raises(LookupError):
        newsletters.set_status(conn, 999, "draft")


# --------------------------------------------------------------------------- articles


@pytest.mark.parametrize(
    "url", ["", "   ", "ftp://site.example/a", "www.site.example/a", "/relatif", "https://"]
)
def test_article_rejects_invalid_url(newsletter: Newsletter, url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        _article(newsletter, url)


def test_article_validation(newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    with pytest.raises(ValueError, match="Rubrique"):
        _article(newsletter, "https://a.example/1", rubric=4)
    with pytest.raises(ValueError, match="Surface"):
        Article(newsletter.id, 1, "T", "S", "https://a.example/1", surface_sqm=0)
    with pytest.raises(ValueError, match="trop long"):
        Article(newsletter.id, 1, "T", "S", "https://a.example/1", actor_label="x" * 101)
    with pytest.raises(ValueError, match="transaction"):
        Article(newsletter.id, 1, "T", "S", "https://a.example/1", transaction_source="ia")
    article = Article(
        newsletter.id,
        1,
        "T",
        "S",
        "https://a.example/1",
        actor_label="  ",
        transaction_evidence="  mot " * 200,
        body_text="x" * 30_000,
    )
    assert article.actor_label is None
    assert len(article.transaction_evidence or "") == 400
    assert article.transaction_evidence is not None
    assert article.transaction_evidence.endswith("...")
    assert len(article.body_text or "") == 20_000


def test_article_duplicate_url_same_month(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    url = "https://www.businessimmo.example/actu/entrepot-lyon"
    added = articles.add(conn, _article(newsletter, url))
    assert added is not None and added.id is not None and added.position == 0
    assert added.collected_at is not None
    assert articles.add(conn, _article(newsletter, url, title="Autre titre")) is None
    assert len(articles.list_for_rubric(conn, newsletter.id, 1)) == 1  # type: ignore[arg-type]

    november = newsletters.get_or_create(conn, "2026-11")
    assert articles.add(conn, _article(november, url)) is not None


def test_sql_check_blocks_direct_invalid_insert(
    conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO articles (newsletter_id, rubric, title, source_name, url, collected_at)"
            " VALUES (?, 1, 'Titre', 'Source', ?, '2026-10-09T00:00:00+00:00')",
            (newsletter.id, "javascript:alert(1)"),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO articles (newsletter_id, rubric, title, source_name, url, collected_at)"
            " VALUES (?, 4, 'Titre', 'Source', 'https://a.example/', 'x')",
            (newsletter.id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO newsletters (month, created_at, updated_at) VALUES ('2026-13', 'x', 'x')"
        )


def test_sql_injection_text_is_stored_verbatim(
    conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    title = "'); DROP TABLE articles; --"
    added = articles.add(conn, _article(newsletter, "https://a.example/x", title=title))
    assert added is not None and added.id is not None
    assert articles.get(conn, added.id).title == title  # type: ignore[union-attr]
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_reorder_and_list(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    ids = [
        articles.add(conn, _article(newsletter, f"https://a.example/{i}", title=f"A{i}")).id  # type: ignore[union-attr]
        for i in range(4)
    ]
    other_rubric = articles.add(conn, _article(newsletter, "https://a.example/r2", rubric=2))
    assert other_rubric is not None and other_rubric.position == 0  # numérotation par rubrique

    articles.reorder(conn, newsletter.id, 1, [ids[3], ids[1]])
    listed = articles.list_for_rubric(conn, newsletter.id, 1)
    assert [a.id for a in listed] == [ids[3], ids[1], ids[0], ids[2]]
    assert [a.position for a in listed] == [0, 1, 2, 3]
    assert [a.rubric for a in articles.list_for_newsletter(conn, newsletter.id)] == [1] * 4 + [2]

    with pytest.raises(ValueError, match="inconnus"):
        articles.reorder(conn, newsletter.id, 1, [other_rubric.id])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="deux fois"):
        articles.reorder(conn, newsletter.id, 1, [ids[0], ids[0]])


def test_update_select_delete(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    a = articles.add(conn, _article(newsletter, "https://a.example/1"))
    b = articles.add(conn, _article(newsletter, "https://a.example/2"))
    assert a is not None and b is not None and a.id is not None and b.id is not None

    a.title = "Nouveau titre"
    a.is_transaction = True
    a.surface_sqm = 42_000
    updated = articles.update(conn, a)
    assert updated.title == "Nouveau titre" and updated.is_transaction is True
    assert updated.transaction_source == "manual"  # modifié à la main
    assert updated.surface_sqm == 42_000
    assert updated.published_at == datetime(2026, 10, 7, 8, 0, tzinfo=UTC)

    b.url = a.url  # le lien n'est jamais modifié par update()
    assert articles.update(conn, b).url == "https://a.example/2"

    articles.set_selected(conn, a.id, False)
    assert [x.id for x in articles.list_for_rubric(conn, newsletter.id, 1, selected_only=True)] == [
        b.id
    ]

    articles.delete(conn, a.id)
    assert articles.get(conn, a.id) is None
    with pytest.raises(LookupError):
        articles.update(conn, a)
    a.id = None
    with pytest.raises(ValueError, match="identifiant"):
        articles.update(conn, a)


def test_auto_transaction_never_overrides_manual(
    conn: sqlite3.Connection, newsletter: Newsletter
) -> None:
    a = articles.add(conn, _article(newsletter, "https://a.example/1"))
    assert a is not None and a.id is not None
    assert articles.apply_auto_transaction(conn, a.id, True, "Phrase source.", ["loue"], 12_000)
    auto = articles.get(conn, a.id)
    assert auto is not None and auto.is_transaction and auto.transaction_source == "auto"
    assert auto.transaction_evidence == "Phrase source." and auto.transaction_terms == ["loue"]
    assert auto.surface_sqm == 12_000

    articles.set_transaction(conn, a.id, False)
    assert not articles.apply_auto_transaction(conn, a.id, True, "Autre phrase.", ["signe"])
    manual = articles.get(conn, a.id)
    assert manual is not None and not manual.is_transaction
    assert manual.transaction_source == "manual"


def test_move_to_rubric(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    actors.sync_from_yaml(conn)
    prologis = actors.get_by_name(conn, "Prologis")
    assert prologis is not None
    a = articles.add(conn, _article(newsletter, "https://a.example/1"))
    articles.add(conn, _article(newsletter, "https://a.example/r2", rubric=2))
    assert a is not None and a.id is not None
    moved = articles.move_to_rubric(conn, a.id, 2, prologis.id, None)
    assert (moved.rubric, moved.actor_id, moved.position) == (2, prologis.id, 1)
    back = articles.move_to_rubric(conn, a.id, 1, None, "Autre acteur")
    assert (back.rubric, back.actor_id, back.actor_label) == (1, None, "Autre acteur")
    with pytest.raises(LookupError):
        articles.move_to_rubric(conn, 999, 1, None, None)


def test_delete_newsletter_cascades(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    nid = newsletter.id
    assert nid is not None
    articles.add(conn, _article(newsletter, "https://a.example/1"))
    run = collection_runs.start(conn, nid)

    newsletters.delete(conn, nid)

    for table in ("articles", "collection_runs"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert newsletters.get(conn, "2026-10") is None
    assert collection_runs.get(conn, run.id) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- collectes


def test_collection_runs(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    assert newsletter.id is not None
    assert collection_runs.last_for_newsletter(conn, newsletter.id) is None
    run = collection_runs.start(conn, newsletter.id)
    assert run.status == "running" and run.id is not None
    assert collection_runs.list_running(conn) == [run]

    finished = collection_runs.finish(
        conn, run.id, "partial", {"articles": 12}, ["Business Immo : délai dépassé."]
    )
    assert finished.status == "partial"
    assert finished.finished_at is not None
    assert finished.stats == {"articles": 12}
    assert finished.errors == ["Business Immo : délai dépassé."]
    assert collection_runs.last_for_newsletter(conn, newsletter.id) == finished
    assert collection_runs.list_running(conn) == []
    with pytest.raises(ValueError):
        collection_runs.finish(conn, run.id, "running")
    with pytest.raises(LookupError):
        collection_runs.finish(conn, 999, "success")


def test_articles_can_reference_actor(conn: sqlite3.Connection, newsletter: Newsletter) -> None:
    actors.sync_from_yaml(conn)
    prologis = actors.get_by_name(conn, "Prologis")
    assert prologis is not None
    article = _article(newsletter, "https://a.example/prologis", rubric=2)
    article.actor_id = prologis.id
    added = articles.add(conn, article)
    assert added is not None and added.actor_id == prologis.id
    with pytest.raises(sqlite3.IntegrityError):  # clé étrangère contrôlée
        article.url = "https://a.example/autre"
        article.actor_id = 9999
        articles.add(conn, article)
