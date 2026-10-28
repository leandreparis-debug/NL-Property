"""Pages du contenu interne : visites et photos, calendrier, absences, documents utiles.

Même socle que la relecture : POST puis redirection avec message, jeton CSRF, saisie
conservée en cas d'erreur (la page est réaffichée avec le message à côté du champ).
"""

from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask.typing import ResponseReturnValue

from news_property.content import service
from news_property.db.connection import get_db
from news_property.models import Absence, Event, Newsletter, Visit, VisitPhoto
from news_property.repositories import absences, events, visits
from news_property.review.service import FormError, moved_order
from news_property.review.views import CardEdit, _limits, _newsletter, _render_month

bp = Blueprint("content", __name__)

NEW_ITEM = 0  # identifiant fictif du formulaire d'ajout (pour réafficher sa saisie)


def _photos_dir() -> object:
    """Dossier des photos (<données>/photos)."""
    return current_app.config["CP_SETTINGS"].photos_dir


def _back(month: str, tab: str, anchor: str = "") -> ResponseReturnValue:
    """Redirection vers l'onglet et l'élément concerné."""
    target = url_for("review.month", month=month, tab=tab)
    return redirect(target + (f"#{anchor}" if anchor else ""))


def _refused(newsletter: Newsletter, tab: str, item_id: int, exc: FormError) -> ResponseReturnValue:
    """Réaffiche l'onglet avec la saisie refusée et le message à côté du champ fautif."""
    edit = CardEdit(item_id, request.form, {exc.field or "general": str(exc)})
    return _render_month(newsletter, tab, "tous", edit, 400)


def _visit(newsletter: Newsletter, visit_id: int) -> Visit:
    """Visite de cette newsletter ; 404 sinon."""
    visit = visits.get(get_db(), visit_id)
    if visit is None or visit.newsletter_id != newsletter.id:
        abort(404)
    return visit


def _photo(newsletter: Newsletter, photo_id: int) -> tuple[VisitPhoto, Visit]:
    """Photo d'une visite de cette newsletter ; 404 sinon."""
    photo = visits.get_photo(get_db(), photo_id)
    if photo is None:
        abort(404)
    return photo, _visit(newsletter, photo.visit_id)


def _event(newsletter: Newsletter, event_id: int) -> Event:
    """Événement de cette newsletter ; 404 sinon."""
    event = events.get(get_db(), event_id)
    if event is None or event.newsletter_id != newsletter.id:
        abort(404)
    return event


def _absence(newsletter: Newsletter, absence_id: int) -> Absence:
    """Absence de cette newsletter ; 404 sinon."""
    absence = absences.get(get_db(), absence_id)
    if absence is None or absence.newsletter_id != newsletter.id:
        abort(404)
    return absence


def _move(items: list[int], item_id: int) -> list[int] | None:
    """Nouvel ordre selon le déplacement demandé (sens, rang) ; None si rien ne change."""
    try:
        return moved_order(
            list(items), item_id, request.form.get("sens", ""), request.form.get("rang")
        )
    except FormError as exc:
        flash(str(exc), "error")
        return None


def _confirm(what: str, label: str, action: str, cancel: str, note: str) -> ResponseReturnValue:
    """Page de confirmation d'une suppression."""
    return render_template(
        "review/confirm_delete.html",
        what=what,
        label=label,
        action=action,
        cancel=cancel,
        note=note,
    )


# --------------------------------------------------------------------------- accès


@bp.get("/contenu-interne")
def start() -> ResponseReturnValue:
    """Bouton « Contenu interne » : visites du mois en préparation (le plus récent au
    statut Brouillon), sinon la liste des newsletters."""
    draft = service.last_draft(get_db())
    if draft is None:
        flash("Aucune newsletter en préparation : ouvrez ou créez d'abord un mois.", "warning")
        return redirect(url_for("review.index"))
    return redirect(url_for("review.month", month=draft.month, tab="visites"))


@bp.get("/photos/<path:file_name>")
def photo_file(file_name: str) -> ResponseReturnValue:
    """Sert une photo du dossier photos ; tout autre chemin (« .. », chemin absolu,
    autre type de fichier) donne 404."""
    if not file_name.endswith(".jpg") or "\\" in file_name or ".." in file_name:
        abort(404)
    return send_from_directory(_photos_dir(), file_name, mimetype="image/jpeg", max_age=3600)


# --------------------------------------------------------------------------- visites


@bp.post("/newsletters/<month>/visites/ajouter")
def add_visit(month: str) -> ResponseReturnValue:
    """Ajoute une visite (en fin de liste)."""
    newsletter = _newsletter(month)
    try:
        visit, warning = service.visit_from_form(request.form, newsletter)
    except FormError as exc:
        return _refused(newsletter, "visites", NEW_ITEM, exc)
    added = visits.add(get_db(), visit)
    flash(
        f"Visite ajoutée : {added.site_name} - {added.location}. Ajoutez-lui des photos.",
        "success",
    )
    if warning:
        flash(warning, "warning")
    return _back(month, "visites", f"visite-{added.id}")


@bp.post("/newsletters/<month>/visites/<int:visit_id>")
def save_visit(month: str, visit_id: int) -> ResponseReturnValue:
    """Enregistre la modification d'une visite."""
    newsletter = _newsletter(month)
    current = _visit(newsletter, visit_id)
    try:
        visit, warning = service.visit_from_form(request.form, newsletter, current)
    except FormError as exc:
        return _refused(newsletter, "visites", visit_id, exc)
    visits.update(get_db(), visit)
    flash("Visite enregistrée.", "success")
    if warning:
        flash(warning, "warning")
    return _back(month, "visites", f"visite-{visit_id}")


@bp.post("/newsletters/<month>/visites/<int:visit_id>/deplacer")
def move_visit(month: str, visit_id: int) -> ResponseReturnValue:
    """Déplace une visite (en tête, monter, descendre, en fin, rang)."""
    newsletter = _newsletter(month)
    _visit(newsletter, visit_id)
    ids = [v.id for v in visits.list_for_newsletter(get_db(), newsletter.id)]  # type: ignore[arg-type]
    order = _move(ids, visit_id)  # type: ignore[arg-type]
    if order is not None:
        visits.reorder(get_db(), newsletter.id, order)  # type: ignore[arg-type]
    return _back(month, "visites", f"visite-{visit_id}")


@bp.route("/newsletters/<month>/visites/<int:visit_id>/supprimer", methods=["GET", "POST"])
def delete_visit(month: str, visit_id: int) -> ResponseReturnValue:
    """Confirmation puis suppression d'une visite et de ses photos (fichiers compris)."""
    newsletter = _newsletter(month)
    visit = _visit(newsletter, visit_id)
    if request.method == "GET":
        return _confirm(
            "cette visite",
            f"{visit.site_name} - {visit.location}",
            url_for("content.delete_visit", month=month, visit_id=visit_id),
            url_for("review.month", month=month, tab="visites") + f"#visite-{visit_id}",
            "La visite et toutes ses photos seront définitivement supprimées.",
        )
    visits.delete(get_db(), visit_id, _photos_dir())  # type: ignore[arg-type]
    flash(f"Visite supprimée : {visit.site_name} - {visit.location}.", "success")
    return _back(month, "visites")


# --------------------------------------------------------------------------- photos


@bp.post("/newsletters/<month>/visites/<int:visit_id>/photos")
def upload_photos(month: str, visit_id: int) -> ResponseReturnValue:
    """Ajoute une ou plusieurs photos à une visite (dans la limite par visite)."""
    newsletter = _newsletter(month)
    visit = _visit(newsletter, visit_id)
    files = [(f.filename or "", f.read()) for f in request.files.getlist("photos")]
    result = service.add_photos(
        get_db(),
        visit,
        files,
        _photos_dir(),  # type: ignore[arg-type]
        newsletter.month,
        _limits().photos_par_visite,
    )
    if result.saved:
        count = len(result.saved)
        flash(
            f"{count} photo{'s' if count > 1 else ''} ajoutée{'s' if count > 1 else ''}.", "success"
        )
    for error in result.errors:
        flash(error, "error")
    return _back(month, "visites", f"visite-{visit_id}")


@bp.post("/newsletters/<month>/photos/<int:photo_id>/legende")
def caption_photo(month: str, photo_id: int) -> ResponseReturnValue:
    """Enregistre la légende d'une photo."""
    newsletter = _newsletter(month)
    photo, visit = _photo(newsletter, photo_id)
    try:
        service.update_caption(get_db(), photo, request.form)
    except FormError as exc:
        flash(f"Légende non enregistrée : {exc}", "error")
    else:
        flash("Légende enregistrée.", "success")
    return _back(month, "visites", f"visite-{visit.id}")


@bp.post("/newsletters/<month>/photos/<int:photo_id>/principale")
def main_photo(month: str, photo_id: int) -> ResponseReturnValue:
    """Choisit la photo principale de la visite (placée en premier)."""
    newsletter = _newsletter(month)
    photo, visit = _photo(newsletter, photo_id)
    service.set_main_photo(get_db(), visit.id, photo_id)  # type: ignore[arg-type]
    flash("Photo principale choisie.", "success")
    return _back(month, "visites", f"visite-{visit.id}")


@bp.route("/newsletters/<month>/photos/<int:photo_id>/supprimer", methods=["GET", "POST"])
def delete_photo(month: str, photo_id: int) -> ResponseReturnValue:
    """Confirmation puis suppression d'une photo (le fichier est supprimé du disque)."""
    newsletter = _newsletter(month)
    photo, visit = _photo(newsletter, photo_id)
    if request.method == "GET":
        return _confirm(
            "cette photo",
            f"Photo de la visite {visit.site_name} - {visit.location}",
            url_for("content.delete_photo", month=month, photo_id=photo_id),
            url_for("review.month", month=month, tab="visites") + f"#visite-{visit.id}",
            "La photo sera définitivement supprimée.",
        )
    visits.delete_photo(get_db(), photo_id, _photos_dir())  # type: ignore[arg-type]
    flash("Photo supprimée.", "success")
    return _back(month, "visites", f"visite-{visit.id}")


# --------------------------------------------------------------------------- calendrier


@bp.post("/newsletters/<month>/calendrier/ajouter")
def add_event(month: str) -> ResponseReturnValue:
    """Ajoute un événement au calendrier."""
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    try:
        event = service.event_from_form(request.form, newsletter.id)
    except FormError as exc:
        return _refused(newsletter, "calendrier", NEW_ITEM, exc)
    added = events.add(get_db(), event)
    flash(f"Événement ajouté : {added.title}.", "success")
    return _back(month, "calendrier", f"evenement-{added.id}")


@bp.post("/newsletters/<month>/calendrier/<int:event_id>")
def save_event(month: str, event_id: int) -> ResponseReturnValue:
    """Enregistre la modification d'un événement."""
    newsletter = _newsletter(month)
    current = _event(newsletter, event_id)
    assert newsletter.id is not None
    try:
        event = service.event_from_form(request.form, newsletter.id, current)
    except FormError as exc:
        return _refused(newsletter, "calendrier", event_id, exc)
    events.update(get_db(), event)
    flash("Événement enregistré.", "success")
    return _back(month, "calendrier", f"evenement-{event_id}")


@bp.post("/newsletters/<month>/calendrier/<int:event_id>/deplacer")
def move_event(month: str, event_id: int) -> ResponseReturnValue:
    """Déplace un événement."""
    newsletter = _newsletter(month)
    _event(newsletter, event_id)
    ids = [e.id for e in events.list_for_newsletter(get_db(), newsletter.id)]  # type: ignore[arg-type]
    order = _move(ids, event_id)  # type: ignore[arg-type]
    if order is not None:
        events.reorder(get_db(), newsletter.id, order)  # type: ignore[arg-type]
    return _back(month, "calendrier", f"evenement-{event_id}")


@bp.post("/newsletters/<month>/calendrier/trier")
def sort_events(month: str) -> ResponseReturnValue:
    """Classe les événements par date (les sans date à la fin)."""
    newsletter = _newsletter(month)
    events.sort_by_date(get_db(), newsletter.id)  # type: ignore[arg-type]
    flash("Calendrier classé par date.", "success")
    return _back(month, "calendrier")


@bp.route("/newsletters/<month>/calendrier/<int:event_id>/supprimer", methods=["GET", "POST"])
def delete_event(month: str, event_id: int) -> ResponseReturnValue:
    """Confirmation puis suppression d'un événement."""
    newsletter = _newsletter(month)
    event = _event(newsletter, event_id)
    if request.method == "GET":
        return _confirm(
            "cet événement",
            event.title,
            url_for("content.delete_event", month=month, event_id=event_id),
            url_for("review.month", month=month, tab="calendrier") + f"#evenement-{event_id}",
            "Il sera retiré du calendrier de ce mois.",
        )
    events.delete(get_db(), event_id)
    flash(f"Événement supprimé : {event.title}.", "success")
    return _back(month, "calendrier")


# --------------------------------------------------------------------------- absences


@bp.post("/newsletters/<month>/absences/ajouter")
def add_absence(month: str) -> ResponseReturnValue:
    """Ajoute un congé ou une absence."""
    newsletter = _newsletter(month)
    assert newsletter.id is not None
    try:
        absence = service.absence_from_form(request.form, newsletter.id)
    except FormError as exc:
        return _refused(newsletter, "absences", NEW_ITEM, exc)
    added = absences.add(get_db(), absence)
    flash(f"Absence ajoutée : {added.person_name}.", "success")
    return _back(month, "absences", f"absence-{added.id}")


@bp.post("/newsletters/<month>/absences/<int:absence_id>")
def save_absence(month: str, absence_id: int) -> ResponseReturnValue:
    """Enregistre la modification d'une absence."""
    newsletter = _newsletter(month)
    current = _absence(newsletter, absence_id)
    assert newsletter.id is not None
    try:
        absence = service.absence_from_form(request.form, newsletter.id, current)
    except FormError as exc:
        return _refused(newsletter, "absences", absence_id, exc)
    absences.update(get_db(), absence)
    flash("Absence enregistrée.", "success")
    return _back(month, "absences", f"absence-{absence_id}")


@bp.post("/newsletters/<month>/absences/<int:absence_id>/deplacer")
def move_absence(month: str, absence_id: int) -> ResponseReturnValue:
    """Déplace une absence."""
    newsletter = _newsletter(month)
    _absence(newsletter, absence_id)
    ids = [a.id for a in absences.list_for_newsletter(get_db(), newsletter.id)]  # type: ignore[arg-type]
    order = _move(ids, absence_id)  # type: ignore[arg-type]
    if order is not None:
        absences.reorder(get_db(), newsletter.id, order)  # type: ignore[arg-type]
    return _back(month, "absences", f"absence-{absence_id}")


@bp.post("/newsletters/<month>/absences/trier")
def sort_absences(month: str) -> ResponseReturnValue:
    """Classe les absences par date de début."""
    newsletter = _newsletter(month)
    absences.sort_by_date(get_db(), newsletter.id)  # type: ignore[arg-type]
    flash("Absences classées par date.", "success")
    return _back(month, "absences")


@bp.route("/newsletters/<month>/absences/<int:absence_id>/supprimer", methods=["GET", "POST"])
def delete_absence(month: str, absence_id: int) -> ResponseReturnValue:
    """Confirmation puis suppression d'une absence."""
    newsletter = _newsletter(month)
    absence = _absence(newsletter, absence_id)
    if request.method == "GET":
        return _confirm(
            "cette absence",
            absence.person_name,
            url_for("content.delete_absence", month=month, absence_id=absence_id),
            url_for("review.month", month=month, tab="absences") + f"#absence-{absence_id}",
            "Elle sera retirée de la rubrique 5 de ce mois.",
        )
    absences.delete(get_db(), absence_id)
    flash(f"Absence supprimée : {absence.person_name}.", "success")
    return _back(month, "absences")


# --------------------------------------------------------------------------- documents


@bp.post("/newsletters/<month>/documents/enregistrer")
def save_documents(month: str) -> ResponseReturnValue:
    """Enregistre les liens des documents utiles (format vérifié, liens jamais appelés)."""
    newsletter = _newsletter(month)
    try:
        saved = service.save_document_links(get_db(), newsletter, request.form)
    except FormError as exc:
        return _refused(newsletter, "documents", NEW_ITEM, exc)
    flash(
        (
            f"{len(saved)} lien(s) enregistré(s). Testez-les avec « Ouvrir »."
            if saved
            else "Aucun lien saisi : la rubrique 6 sera vide."
        ),
        "success",
    )
    return _back(month, "documents")


@bp.post("/newsletters/<month>/documents/retirer")
def clear_documents(month: str) -> ResponseReturnValue:
    """Retire tous les liens des documents utiles."""
    newsletter = _newsletter(month)
    service.clear_document_links(get_db(), newsletter)
    flash("Liens des documents retirés.", "success")
    return _back(month, "documents")
