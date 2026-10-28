"""Pages de relecture : liste des newsletters, relecture d'un mois, ajouts manuels.

Toute action qui modifie passe par POST puis redirection (PRG), avec un message de
confirmation ou d'erreur. Les formulaires sont protégés par jeton CSRF.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue

from news_property.collect.periods import PARIS, previous_month
from news_property.collect.views import app_now, month_label
from news_property.content import service as content_service
from news_property.content.pages import CONTENT_TABS, tab_context
from news_property.content.pages import TEMPLATES as CONTENT_TEMPLATES
from news_property.db.connection import get_db
from news_property.detect.reporting import format_sqm
from news_property.http_client import Fetcher
from news_property.models import Article, Newsletter, validate_month
from news_property.repositories import actors, articles, newsletters
from news_property.review import service
from news_property.review.limits import LIMITS_FILE, Limits, load_limits
from news_property.review.service import FormError, LinkError

bp = Blueprint("review", __name__, url_prefix="/newsletters")

TABS = {"rubrique-1": 1, "rubrique-2": 2}
# Onglets de l'écran du mois, dans l'ordre de la newsletter.
TAB_LABELS = {
    "une": "Une",
    "rubrique-1": "1. Immobilier logistique",
    "rubrique-2": "2. Acteurs du marché",
    "visites": "3. Visites d'entrepôts",
    "calendrier": "4. Calendrier",
    "absences": "5. Congés et absences",
    "documents": "6. Documents utiles",
}
ARTICLE_FILTERS = {
    "tous": "Tout",
    "retenus": "Retenus",
    "non-retenus": "Non retenus",
    "transactions": "Transactions",
}


@dataclass
class CardEdit:
    """Carte dont l'enregistrement a été refusé : valeurs saisies et erreurs par champ."""

    item_id: int
    values: Mapping[str, str]
    errors: dict[str, str]


# --------------------------------------------------------------------------- outils


def _limits() -> Limits:
    """Limites relues à chaque affichage ; un fichier invalide est signalé une fois par page."""
    limits, error = load_limits(current_app.config.get("CP_LIMITS_FILE", LIMITS_FILE))
    if error:
        flash(error, "warning")
    return limits


def _newsletter(month: str) -> Newsletter:
    """Newsletter du mois ; 404 si le mois est invalide ou n'existe pas."""
    try:
        month = validate_month(month)
    except ValueError:
        abort(404)
    newsletter = newsletters.get(get_db(), month)
    if newsletter is None or newsletter.id is None:
        abort(404)
    return newsletter


def _article(newsletter: Newsletter, article_id: int) -> Article:
    """Article de cette newsletter ; 404 sinon."""
    article = articles.get(get_db(), article_id)
    if article is None or article.newsletter_id != newsletter.id:
        abort(404)
    return article


def _tab_of(article: Article) -> str:
    """Onglet d'un article."""
    return f"rubrique-{article.rubric}"


def _back(month: str, tab: str, anchor: str = "") -> ResponseReturnValue:
    """Redirection vers l'onglet (en gardant le filtre choisi) et l'élément concerné."""
    chosen = request.form.get("filtre", "tous")
    query = {"filtre": chosen} if chosen in ARTICLE_FILTERS and chosen != "tous" else {}
    target = url_for("review.month", month=month, tab=tab, **query)
    return redirect(target + (f"#{anchor}" if anchor else ""))


def _fetcher() -> Fetcher:
    """Récupérateur HTTP (injectable dans les tests via CP_FETCHER_FACTORY)."""
    return current_app.config.get("CP_FETCHER_FACTORY", Fetcher)()


def _actor_list() -> list[Any]:
    """Acteurs suivis, pour la liste déroulante."""
    return actors.list_all(get_db(), tracked_only=True)


def _date_value(article: Article) -> str:
    """Valeur du champ date (AAAA-MM-JJ, jour de Paris)."""
    return article.published_at.astimezone(PARIS).date().isoformat() if article.published_at else ""


def _filter_articles(items: list[Article], chosen: str) -> list[Article]:
    """Articles selon le filtre choisi."""
    if chosen == "retenus":
        return [a for a in items if a.selected]
    if chosen == "non-retenus":
        return [a for a in items if not a.selected]
    if chosen == "transactions":
        return [a for a in items if a.is_transaction]
    return items


# --------------------------------------------------------------------------- liste


@bp.get("/")
def index() -> ResponseReturnValue:
    """Liste des newsletters (de la plus récente à la plus ancienne)."""
    return render_template(
        "review/list.html",
        summaries=service.list_newsletters(get_db()),
        default_month=previous_month(app_now()),
        month_label=month_label,
    )


@bp.post("/")
def open_month() -> ResponseReturnValue:
    """Crée (si besoin) puis ouvre la newsletter du mois choisi."""
    try:
        month = validate_month((request.form.get("mois") or "").strip())
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("review.index"))
    existed = newsletters.get(get_db(), month) is not None
    newsletters.get_or_create(get_db(), month)
    if not existed:
        flash(f"Newsletter de {month_label(month)} créée.", "success")
    return redirect(url_for("review.month", month=month, tab="rubrique-1"))


# --------------------------------------------------------------------------- relecture


@bp.get("/<month>/")
def month_home(month: str) -> ResponseReturnValue:
    """Ouvre le premier onglet."""
    return redirect(url_for("review.month", month=month, tab="rubrique-1"))


@bp.get("/<month>/<tab>")
def month(month: str, tab: str) -> ResponseReturnValue:
    """Écran du mois : un onglet par rubrique."""
    if tab not in TAB_LABELS:
        abort(404)
    return _render_month(_newsletter(month), tab, request.args.get("filtre", "tous"))


def _tabs(
    conn: Any, newsletter: Newsletter, counts: service.Counts, limits: Limits
) -> list[tuple[str, str, int | None, int | None]]:
    """Onglets (clé, libellé, compteur, limite) ; compteur et limite None si sans objet."""
    assert newsletter.id is not None
    content = content_service.content_counts(conn, newsletter.id)
    numbers: dict[str, tuple[int | None, int | None]] = {
        "rubrique-1": (counts.selected_1, limits.rubrique_1),
        "rubrique-2": (counts.selected_2, limits.rubrique_2),
        "visites": (content["visites"], limits.visites),
        "calendrier": (content["calendrier"], limits.evenements),
        "absences": (content["absences"], limits.absences),
        "documents": (content["documents"], None),
    }
    return [(key, label, *numbers.get(key, (None, None))) for key, label in TAB_LABELS.items()]


def _render_month(
    newsletter: Newsletter,
    tab: str,
    chosen: str,
    edit: CardEdit | None = None,
    status: int = 200,
) -> ResponseReturnValue:
    """Affiche un onglet. edit : carte refusée à réafficher avec la saisie et les erreurs."""
    assert newsletter.id is not None
    conn = get_db()
    limits = _limits()
    counts = service.count_selected(conn, newsletter.id)
    context: dict[str, Any] = {
        "newsletter": newsletter,
        "month_label": month_label(newsletter.month),
        "status_label": service.STATUS_LABELS.get(newsletter.status, newsletter.status),
        "tab": tab,
        "tabs": _tabs(conn, newsletter, counts, limits),
        "limits": limits,
        "edit": edit,
        "unfold_all": request.args.get("deplier") == "1",
    }
    if tab in CONTENT_TABS:
        context.update(tab_context(conn, newsletter, tab, limits))
        return render_template(CONTENT_TEMPLATES[tab], **context), status

    rubric = TABS[tab]
    chosen = chosen if chosen in ARTICLE_FILTERS else "tous"
    items = articles.list_for_rubric(conn, newsletter.id, rubric)
    context.update(
        rubric=rubric,
        other_rubric=2 if rubric == 1 else 1,
        filters=ARTICLE_FILTERS,
        filtre=chosen,
        items=_filter_articles(items, chosen),
        total=len(items),
        count=counts.for_rubric(rubric),
        limit=limits.for_rubric(rubric),
        positions={a.id: i for i, a in enumerate(items)},
        actor_list=_actor_list(),
        actor_names={a.id: a.name for a in actors.list_all(conn)},
        date_value=_date_value,
        other_actor=service.OTHER_ACTOR,
        format_sqm=format_sqm,
    )
    return render_template("review/articles.html", **context), status


@bp.post("/<month>/statut")
def set_status(month: str) -> ResponseReturnValue:
    """Marque la newsletter comme relue (avec avertissements) ou la repasse en brouillon."""
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    tab = request.form.get("onglet", "rubrique-1")
    tab = tab if tab in TAB_LABELS else "rubrique-1"
    if request.form.get("statut") == "relue":
        for warning in service.review_warnings(get_db(), newsletter.id, _limits()):
            flash(warning, "warning")
        newsletters.set_status(get_db(), newsletter.id, "reviewed")
        flash(f"Newsletter de {month_label(month)} marquée comme relue.", "success")
    elif request.form.get("statut") == "brouillon":
        newsletters.set_status(get_db(), newsletter.id, "draft")
        flash(f"Newsletter de {month_label(month)} repassée en brouillon.", "success")
    else:
        flash("Action inconnue.", "error")
    return redirect(url_for("review.month", month=month, tab=tab))


# --------------------------------------------------------------------------- articles


@bp.post("/<month>/articles/<int:article_id>")
def save_article(month: str, article_id: int) -> ResponseReturnValue:
    """Enregistre la carte d'un article (le lien n'est jamais modifié).

    En cas d'erreur, la page est réaffichée avec toute la saisie de la carte et le
    message à côté du champ fautif (rien n'est perdu).
    """
    newsletter = _newsletter(month)
    article = _article(newsletter, article_id)
    try:
        updated = service.apply_article_form(article, request.form, _actor_list())
        articles.update(get_db(), updated)
    except FormError as exc:
        edit = CardEdit(article_id, request.form, {exc.field or "general": str(exc)})
        return _render_month(
            newsletter, _tab_of(article), request.form.get("filtre", "tous"), edit, 400
        )
    flash("Article enregistré.", "success")
    return _back(month, _tab_of(article), f"article-{article_id}")


@bp.post("/<month>/articles/<int:article_id>/retenir")
def toggle_article(month: str, article_id: int) -> ResponseReturnValue:
    """Retient ou ne retient plus un article (bouton rapide)."""
    newsletter = _newsletter(month)
    article = _article(newsletter, article_id)
    keep = request.form.get("valeur") == "1"
    articles.set_selected(get_db(), article_id, keep)
    flash("Article retenu." if keep else "Article retiré de la newsletter.", "success")
    return _back(month, _tab_of(article), f"article-{article_id}")


@bp.post("/<month>/articles/<int:article_id>/deplacer")
def move_article(month: str, article_id: int) -> ResponseReturnValue:
    """Déplace un article : monter, descendre, en tête, en fin ou à un rang donné."""
    newsletter = _newsletter(month)
    article = _article(newsletter, article_id)
    try:
        moved = service.move_article(
            get_db(), article, request.form.get("sens", ""), request.form.get("rang")
        )
    except FormError as exc:
        flash(str(exc), "error")
    else:
        if not moved:
            flash("L'article est déjà à cette place.", "warning")
    return _back(month, _tab_of(article), f"article-{article_id}")


@bp.post("/<month>/articles/<int:article_id>/rubrique")
def change_rubric(month: str, article_id: int) -> ResponseReturnValue:
    """Déplace un article vers l'autre rubrique (en fin de liste)."""
    newsletter = _newsletter(month)
    article = _article(newsletter, article_id)
    try:
        moved = service.change_rubric(
            get_db(), article, request.form.get("cible"), request.form, _actor_list()
        )
    except FormError as exc:
        flash(f"Article non déplacé : {exc}", "error")
        return _back(month, _tab_of(article), f"article-{article_id}")
    flash(
        f"Article déplacé en fin de {service.RUBRIC_TITLES[moved.rubric]} : « {moved.title} ».",
        "success",
    )
    return _back(month, _tab_of(article))


@bp.route("/<month>/articles/<int:article_id>/supprimer", methods=["GET", "POST"])
def delete_article(month: str, article_id: int) -> ResponseReturnValue:
    """Confirmation (GET) puis suppression (POST) d'un article."""
    newsletter = _newsletter(month)
    article = _article(newsletter, article_id)
    if request.method == "GET":
        return render_template(
            "review/confirm_delete.html",
            month=month,
            what="cet article",
            label=article.title,
            action=url_for("review.delete_article", month=month, article_id=article_id),
            cancel=url_for("review.month", month=month, tab=_tab_of(article))
            + f"#article-{article_id}",
            note="L'article sera retiré de la base. Une prochaine collecte pourrait le "
            "retrouver ; pour l'écarter simplement, préférez « Ne plus retenir ».",
        )
    articles.delete(get_db(), article_id)
    flash(f"Article supprimé : « {article.title} ».", "success")
    return _back(month, _tab_of(article))


@bp.post("/<month>/<tab>/garder-premiers")
def keep_first(month: str, tab: str) -> ResponseReturnValue:
    """Ne retient que les N premiers articles de la rubrique (N = limite)."""
    if tab not in TABS:
        abort(404)
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    limit = _limits().for_rubric(TABS[tab])
    kept = service.keep_first(get_db(), newsletter.id, TABS[tab], limit)
    flash(f"Seuls les {kept} premiers articles sont retenus.", "success")
    return _back(month, tab)


# --------------------------------------------------------------------------- ajout d'article


def _add_article_page(
    newsletter: Newsletter,
    *,
    step: int,
    form: Mapping[str, str],
    preview: service.PagePreview | None = None,
    error: str | None = None,
    status: int = 200,
    refused: str | None = None,
    field: str | None = None,
) -> ResponseReturnValue:
    """Formulaire d'ajout d'un article (étape 1 : lien ; étape 2 : compléter).

    refused : message si le site refuse la lecture automatique (confirmation demandée).
    """
    return (
        render_template(
            "review/add_article.html",
            newsletter=newsletter,
            month_label=month_label(newsletter.month),
            step=step,
            form=form,
            preview=preview,
            error=error,
            actor_list=_actor_list(),
            other_actor=service.OTHER_ACTOR,
            refused=refused,
            error_field=field,
        ),
        status,
    )


@bp.get("/<month>/ajouter-article")
def add_article(month: str) -> ResponseReturnValue:
    """Étape 1 : saisie du lien de l'article."""
    newsletter = _newsletter(month)
    rubric = "2" if request.args.get("rubrique") == "2" else "1"
    return _add_article_page(newsletter, step=1, form={"rubrique": rubric})


@bp.post("/<month>/ajouter-article/verifier")
def check_article(month: str) -> ResponseReturnValue:
    """Étape 2 : vérifie le lien et pré-remplit le formulaire (rien n'est enregistré).

    Si le site refuse la lecture automatique, l'étape 2 demande à l'utilisateur de
    confirmer le lien lui-même et de saisir titre et premières lignes.
    """
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    form = dict(request.form)
    try:
        with _fetcher() as fetcher:
            preview = service.preview_link(fetcher, form.get("lien", ""))
    except service.LinkRefused as exc:
        if service.find_duplicate(get_db(), newsletter.id, exc.final_url) is not None:
            return _add_article_page(
                newsletter,
                step=1,
                form=form,
                error="Cet article est déjà dans la newsletter de ce mois.",
                status=400,
            )
        prefill = {"rubrique": form.get("rubrique", "1"), "lien": form.get("lien", "").strip()}
        return _add_article_page(newsletter, step=2, form=prefill, refused=str(exc))
    except LinkError as exc:
        return _add_article_page(newsletter, step=1, form=form, error=str(exc), status=400)
    duplicate = service.find_duplicate(get_db(), newsletter.id, preview.url)
    if duplicate is not None:
        return _add_article_page(
            newsletter,
            step=1,
            form=form,
            error=f"Cet article est déjà dans la newsletter : « {duplicate.title} ».",
            status=400,
        )
    prefill = {
        "rubrique": form.get("rubrique", "1"),
        "titre": preview.title,
        "source": preview.source_name,
        "extrait": preview.excerpt,
        "date": preview.published.isoformat() if preview.published else "",
        "lien": preview.url,
    }
    return _add_article_page(newsletter, step=2, form=prefill, preview=preview)


@bp.post("/<month>/ajouter-article/enregistrer")
def save_new_article(month: str) -> ResponseReturnValue:
    """Enregistre l'article ajouté à la main ; le lien est revérifié à ce moment-là."""
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    form = dict(request.form)
    refused: str | None = None
    try:
        try:
            with _fetcher() as fetcher:
                preview: service.PagePreview | None = service.preview_link(
                    fetcher, form.get("lien", "")
                )
        except service.LinkRefused as exc:
            preview, refused = None, str(exc)
            article = service.add_manual_article(
                get_db(), newsletter.id, None, form, _actor_list(), refused_url=exc.final_url
            )
        else:
            article = service.add_manual_article(
                get_db(), newsletter.id, preview, form, _actor_list()
            )
    except LinkError as exc:
        return _add_article_page(newsletter, step=1, form=form, error=str(exc), status=400)
    except FormError as exc:
        return _add_article_page(
            newsletter,
            step=2,
            form=form,
            error=str(exc),
            status=400,
            refused=refused,
            field=exc.field,
        )
    flash(f"Article ajouté : « {article.title} ».", "success")
    return redirect(
        url_for("review.month", month=month, tab=_tab_of(article)) + f"#article-{article.id}"
    )
