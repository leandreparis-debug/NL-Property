"""Client HTTP « poli » réutilisable : User-Agent explicite, reprises, débit limité, robots.txt."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from news_property.version import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"NewsProperty/{__version__} (veille interne)"
ROBOTS_AGENT = "NewsProperty"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 2
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_BACKOFF = 1.0


@dataclass
class FetchResult:
    """Résultat d'une requête HTTP. Ne lève jamais d'exception : les erreurs sont dans error.

    Attributs :
        url : URL demandée.
        final_url : URL finale après redirections (None si aucune réponse).
        status_code : statut HTTP final (None si aucune réponse).
        content_type : type de contenu annoncé (sans paramètres, ex. « text/html »).
        text : contenu décodé en texte.
        content : contenu brut (utile pour les flux, dont l'encodage est dans le XML).
        error : message d'erreur en français, ou None si la réponse est un succès 2xx.
        blocked_by_robots : True si la page n'a pas été demandée à cause de robots.txt.
    """

    url: str
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    text: str = ""
    content: bytes = field(default=b"", repr=False)
    error: str | None = None
    blocked_by_robots: bool = False

    @property
    def ok(self) -> bool:
        """Vrai si la requête a abouti avec un statut 2xx."""
        return self.error is None and self.status_code is not None and 200 <= self.status_code < 300


def create_client(
    timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Crée un httpx.Client configuré : User-Agent du projet, délai, redirections suivies."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    )


def describe_status(status_code: int) -> str:
    """Traduit un statut HTTP d'erreur en une phrase française."""
    if status_code in (401, 403):
        return f"Le site a refusé l'accès (HTTP {status_code})."
    if status_code == 404:
        return "Page introuvable (HTTP 404)."
    if status_code == 410:
        return "Page supprimée (HTTP 410)."
    if status_code == 429:
        return "Le site limite le nombre de requêtes (HTTP 429)."
    if status_code >= 500:
        return f"Le site est en erreur (HTTP {status_code})."
    return f"Réponse inattendue du site (HTTP {status_code})."


def describe_exception(exc: Exception, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Traduit une exception réseau httpx en une phrase française."""
    if isinstance(exc, httpx.TimeoutException):
        return f"Le site n'a pas répondu à temps (délai de {timeout:g} s dépassé)."
    if isinstance(exc, httpx.ProxyError):
        return "Le proxy réseau a refusé ou n'a pas pu établir la connexion au site."
    if isinstance(exc, httpx.ConnectError):
        return "Connexion impossible au site (adresse introuvable ou serveur injoignable)."
    if isinstance(exc, httpx.TooManyRedirects):
        return "Le site redirige en boucle (trop de redirections)."
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return "Adresse invalide."
    return f"Erreur réseau : {exc.__class__.__name__}."


def _origin(url: str) -> str:
    """Schéma et domaine d'une URL (ex. https://www.exemple.fr)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _host(url: str) -> str:
    """Nom d'hôte (en minuscules) d'une URL."""
    return (urlsplit(url).hostname or "").lower()


class Fetcher:
    """Récupère des URLs poliment.

    - au plus une requête par seconde (par défaut) vers un même domaine ;
    - jusqu'à 2 nouvelles tentatives sur erreur réseau ou réponse 5xx ;
    - vérification de robots.txt (mise en cache par domaine) lorsque check_robots=True.

    Les fonctions sleep et clock sont injectables pour les tests.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        retries: int = DEFAULT_RETRIES,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        backoff: float = DEFAULT_BACKOFF,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Prépare le récupérateur (crée un client par défaut si besoin)."""
        self._owns_client = client is None
        self.client = client if client is not None else create_client()
        self.retries = retries
        self.min_interval = min_interval
        self.backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_denied: dict[str, int] = {}
        self._lock = threading.Lock()

    def __enter__(self) -> Fetcher:
        """Permet l'usage avec « with »."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Ferme le client à la sortie du bloc « with »."""
        self.close()

    def close(self) -> None:
        """Ferme le client HTTP s'il a été créé par ce récupérateur."""
        if self._owns_client:
            self.client.close()

    def fetch(self, url: str, *, check_robots: bool = False) -> FetchResult:
        """Récupère une URL et renvoie un FetchResult (jamais d'exception réseau).

        check_robots=True : à utiliser pour les pages HTML ; la page n'est pas demandée
        si robots.txt l'interdit à notre robot.
        """
        if check_robots and not self.allowed_by_robots(url):
            denied_status = self._robots_denied.get(_origin(url))
            if denied_status is not None:
                error = (
                    f"Le site a refusé l'accès (HTTP {denied_status}) à son fichier robots.txt : "
                    "par prudence, la page n'est pas consultée."
                )
            else:
                error = "Page non consultée : le fichier robots.txt du site l'interdit."
            return FetchResult(url=url, error=error, blocked_by_robots=True)
        return self._get(url)

    def allowed_by_robots(self, url: str) -> bool:
        """Indique si robots.txt autorise notre robot à consulter l'URL (cache par domaine)."""
        origin = _origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """Télécharge et analyse robots.txt. None signifie « aucune restriction »."""
        result = self._get(f"{origin}/robots.txt")
        parser = RobotFileParser()
        if result.status_code in (401, 403):
            # Convention de la bibliothèque standard : accès refusé à robots.txt = tout interdit.
            self._robots_denied[origin] = result.status_code
            parser.disallow_all = True
            return parser
        if not result.ok:
            return None
        parser.parse(result.text.splitlines())
        return parser

    def _throttle(self, url: str) -> None:
        """Attend si la dernière requête vers le même domaine est trop récente."""
        host = _host(url)
        with self._lock:
            last = self._last_request.get(host)
            if last is not None:
                wait = self.min_interval - (self._clock() - last)
                if wait > 0:
                    self._sleep(wait)
            self._last_request[host] = self._clock()

    def _get(self, url: str) -> FetchResult:
        """GET avec reprises sur erreur réseau ou 5xx."""
        attempts = self.retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                self._sleep(self.backoff * (attempt - 1))
            self._throttle(url)
            try:
                response = self.client.get(url)
            except httpx.UnsupportedProtocol as exc:
                return FetchResult(url=url, error=describe_exception(exc, self._timeout()))
            except httpx.TransportError as exc:
                last_error = describe_exception(exc, self._timeout())
                logger.info("Tentative %d/%d échouée pour %s : %r", attempt, attempts, url, exc)
                continue
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                # Redirections en boucle, URL invalide... : inutile de réessayer.
                return FetchResult(url=url, error=describe_exception(exc, self._timeout()))
            if response.status_code >= 500 and attempt < attempts:
                logger.info(
                    "Tentative %d/%d : HTTP %d pour %s",
                    attempt,
                    attempts,
                    response.status_code,
                    url,
                )
                continue
            return self._to_result(url, response)
        return FetchResult(url=url, error=last_error)

    def _timeout(self) -> float:
        """Délai de lecture configuré sur le client, pour les messages d'erreur."""
        return self.client.timeout.read or DEFAULT_TIMEOUT

    @staticmethod
    def _to_result(url: str, response: httpx.Response) -> FetchResult:
        """Convertit une réponse httpx en FetchResult."""
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        status = response.status_code
        try:
            text = response.text
        except (LookupError, ValueError):
            text = response.content.decode("utf-8", errors="replace")
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=status,
            content_type=content_type or None,
            text=text,
            content=response.content,
            error=None if 200 <= status < 300 else describe_status(status),
        )


_default_fetcher: Fetcher | None = None


def get_default_fetcher() -> Fetcher:
    """Renvoie le récupérateur partagé du processus (créé au premier appel)."""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = Fetcher()
    return _default_fetcher


def fetch(url: str, *, check_robots: bool = False) -> FetchResult:
    """Récupère une URL avec le récupérateur partagé (voir Fetcher.fetch)."""
    return get_default_fetcher().fetch(url, check_robots=check_robots)
