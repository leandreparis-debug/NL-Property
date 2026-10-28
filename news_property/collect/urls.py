"""Nettoyage et normalisation des URL d'articles (dédoublonnage)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset({"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"})


def _is_tracking(name: str) -> bool:
    """Paramètre de suivi publicitaire (utm_*, fbclid, gclid...)."""
    name = name.lower()
    return name.startswith("utm_") or name in TRACKING_PARAMS


def clean_url(url: str) -> str:
    """URL à stocker : schéma et hôte en minuscules, sans fragment ni paramètres de suivi.

    Le chemin est conservé tel quel (y compris le « / » final), pour que le lien reste
    exactement celui de l'éditeur.
    """
    parts = urlsplit(url.strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def url_key(url: str) -> str:
    """Clé de dédoublonnage : URL nettoyée, sans « / » final, schéma http/https confondus."""
    parts = urlsplit(clean_url(url))
    path = parts.path.rstrip("/")
    return urlunsplit(("", parts.netloc.removeprefix("www."), path, parts.query, ""))
