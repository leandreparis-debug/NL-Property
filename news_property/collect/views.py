"""Pages web de la collecte : choix du mois, lancement en arrière-plan, suivi et bilan."""

from __future__ import annotations

import logging
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask.typing import ResponseReturnValue

from news_property.collect.config import (
    RULES_FILE,
    SOURCES_FILE,
    load_rules,
    load_sources,
)
from news_property.collect.periods import previous_month
from news_property.collect.runner import (
    STALE_RUN_AFTER,
    CollectionAlreadyRunning,
    CollectionReport,
    Progress,
    run_collection,
    system_clock,
)
from news_property.config import ConfigError
from news_property.dates import format_month_fr
from news_property.db.connection import get_connection, get_db
from news_property.http_client import Fetcher
from news_property.models import validate_month
from news_property.repositories import collection_runs, newsletters

logger = logging.getLogger(__name__)

bp = Blueprint("collect", __name__, url_prefix="/collecte")

STATUS_LABELS = {
    "running": "En cours",
    "success": "Terminée",
    "partial": "Terminée, avec des sources en erreur",
    "failed": "Échouée",
}
REFRESH_SECONDS = 2


@dataclass
class CollectionJob:
    """Collecte lancée depuis l'interface (une seule à la fois par application)."""

    month: str
    state: str = "running"  # running | done | error
    progress: Progress | None = None
    report: CollectionReport | None = None
    error: str | None = None
    thread: threading.Thread | None = field(default=None, repr=False)


@dataclass
class _Registry:
    """Collecte en cours ou dernière collecte terminée de l'application."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    job: CollectionJob | None = None


def _registry(app: Flask) -> _Registry:
    """Registre des collectes de l'application (créé au premier appel)."""
    return app.extensions.setdefault("cp_collect", _Registry())


def app_now() -> datetime:
    """Date et heure courantes de l'application (horloge injectable via
    app.config["CP_CLOCK"])."""
    return current_app.config.get("CP_CLOCK", system_clock)()


def month_label(month: str) -> str:
    """« 2026-10 » → « Octobre 2026 »."""
    year, number = (int(part) for part in month.split("-"))
    return format_month_fr(datetime(year, number, 1).date())


def _run_job(app: Flask, job: CollectionJob) -> None:
    """Corps du fil d'exécution : lance la collecte avec sa propre connexion."""
    settings = app.config["CP_SETTINGS"]
    fetcher_factory = app.config.get("CP_FETCHER_FACTORY", Fetcher)
    clock = app.config.get("CP_CLOCK", system_clock)

    def on_progress(progress: Progress) -> None:
        job.progress = progress

    try:
        with closing(get_connection(settings)) as conn, fetcher_factory() as fetcher:
            job.report = run_collection(
                conn,
                settings,
                job.month,
                clock,
                fetcher=fetcher,
                sources_path=app.config.get("CP_SOURCES_FILE", SOURCES_FILE),
                rules_path=app.config.get("CP_RULES_FILE", RULES_FILE),
                on_progress=on_progress,
            )
        job.state = "done"
    except (ConfigError, CollectionAlreadyRunning) as exc:
        job.error, job.state = str(exc), "error"
    except Exception:
        logger.exception("Collecte du mois %s interrompue", job.month)
        job.error = (
            "La collecte s'est arrêtée sur une erreur inattendue. "
            "Le détail technique est dans le journal de l'application."
        )
        job.state = "error"


def _check_config() -> str | None:
    """Vérifie sources.yaml et regles_veille.yaml avant de lancer ; message d'erreur ou None."""
    try:
        load_sources(current_app.config.get("CP_SOURCES_FILE", SOURCES_FILE))
        load_rules(current_app.config.get("CP_RULES_FILE", RULES_FILE))
    except ConfigError as exc:
        return str(exc)
    return None


def _running_elsewhere() -> str | None:
    """Message si une collecte récente est marquée en cours en base (autre fenêtre, commande)."""
    now = app_now()
    for run in collection_runs.list_running(get_db()):
        if now - run.started_at < STALE_RUN_AFTER:
            return (
                "Une collecte est déjà en cours (lancée le "
                f"{run.started_at.astimezone():%d/%m/%Y à %H:%M}). "
                "Attendez qu'elle se termine avant d'en lancer une autre."
            )
    return None


def start_job(app: Flask, month: str) -> CollectionJob:
    """Lance une collecte en arrière-plan.

    Lève CollectionAlreadyRunning si une collecte de l'application est déjà en cours.
    """
    registry = _registry(app)
    with registry.lock:
        if registry.job is not None and registry.job.state == "running":
            raise CollectionAlreadyRunning(
                f"Une collecte ({month_label(registry.job.month)}) est déjà en cours. "
                "Attendez qu'elle se termine avant d'en lancer une autre."
            )
        job = CollectionJob(month=month)
        job.thread = threading.Thread(
            target=_run_job, args=(app, job), name=f"collecte-{month}", daemon=True
        )
        registry.job = job
        job.thread.start()
    return job


def _last_run_summary(month: str) -> dict[str, Any] | None:
    """Bilan de la dernière collecte enregistrée en base pour ce mois (s'il y en a une)."""
    newsletter = newsletters.get(get_db(), month)
    if newsletter is None or newsletter.id is None:
        return None
    run = collection_runs.last_for_newsletter(get_db(), newsletter.id)
    if run is None:
        return None
    return {
        "status": run.status,
        "status_label": STATUS_LABELS.get(run.status, run.status),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stats": run.stats,
        "errors": run.errors,
    }


@bp.get("/")
def index() -> ResponseReturnValue:
    """Formulaire : choix du mois (par défaut le mois précédent) et lancement."""
    month = request.args.get("mois") or previous_month(app_now())
    try:
        month = validate_month(month)
    except ValueError:
        month = previous_month(app_now())
    job = _registry(current_app).job
    return render_template(
        "collect/index.html",
        month=month,
        month_label=month_label(month),
        job=job if job and job.state == "running" else None,
        config_error=_check_config(),
        last_run=_last_run_summary(month),
        error=None,
    )


@bp.post("/")
def start() -> ResponseReturnValue:
    """Lance la collecte du mois choisi et redirige vers la page de suivi."""
    raw_month = (request.form.get("mois") or "").strip()
    error: str | None = None
    try:
        month = validate_month(raw_month)
    except ValueError as exc:
        month, error = previous_month(app_now()), str(exc)
    error = error or _check_config() or _running_elsewhere()
    if error is None:
        try:
            start_job(current_app._get_current_object(), month)  # type: ignore[attr-defined]
        except CollectionAlreadyRunning as exc:
            error = str(exc)
    if error is not None:
        return (
            render_template(
                "collect/index.html",
                month=month,
                month_label=month_label(month),
                job=None,
                config_error=None,
                last_run=None,
                error=error,
            ),
            409 if "déjà en cours" in error else 400,
        )
    return redirect(url_for("collect.status"))


@bp.get("/suivi")
def status() -> ResponseReturnValue:
    """Avancement de la collecte (rafraîchissement automatique), puis bilan."""
    job = _registry(current_app).job
    if job is None:
        return redirect(url_for("collect.index"))
    summary = None
    if job.report is not None:
        report = job.report
        summary = {
            "status": report.status,
            "status_label": STATUS_LABELS.get(report.status, report.status),
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "stats": report.to_stats(),
            "errors": report.errors,
        }
    return render_template(
        "collect/status.html",
        job=job,
        month_label=month_label(job.month),
        summary=summary,
        refresh=REFRESH_SECONDS if job.state == "running" else None,
    )
