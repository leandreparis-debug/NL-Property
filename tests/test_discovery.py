"""Tests de la découverte des flux."""

from __future__ import annotations

import httpx
import respx

from news_property.http_client import Fetcher
from news_property.sources.discovery import (
    discover_feeds,
    discover_feeds_detailed,
    extract_declared_feeds,
)
from tests.helpers import fixture_bytes, html, rss, serve

SITE = "https://immolog-test.example"


def test_finds_declared_feed(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    serve(
        mock_http,
        {
            f"{SITE}/": html("site_with_link.html"),
            f"{SITE}/actus/rss.xml": rss("rss_complete.xml"),
        },
    )
    assert discover_feeds(SITE, fetcher) == [f"{SITE}/actus/rss.xml"]


def test_finds_feed_on_common_path(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    site = "https://nofeedlink.example"
    serve(
        mock_http,
        {
            f"{site}/": html("site_without_feed.html"),
            f"{site}/feed": rss("rss_complete.xml"),
            # /feed/ renvoie le même flux : doit être dédoublonné.
            f"{site}/feed/": rss("rss_complete.xml"),
        },
    )
    assert discover_feeds(site, fetcher) == [f"{site}/feed"]


def test_no_feed_returns_empty_list(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    site = "https://nofeed.example"
    # Le site renvoie sa page d'accueil HTML pour toutes les adresses.
    serve(mock_http, {}, fallback=html("site_without_feed.html"))
    assert discover_feeds(site, fetcher) == []


def test_malformed_html_does_not_crash(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    site = "https://broken.example"
    serve(mock_http, {f"{site}/": html("malformed.html")})
    assert discover_feeds(site, fetcher) == []
    assert extract_declared_feeds(fixture_bytes("malformed.html").decode(), site) == []


def test_homepage_error_still_tries_common_paths(
    mock_http: respx.MockRouter, fetcher: Fetcher
) -> None:
    site = "https://blocked.example"
    serve(mock_http, {f"{site}/": httpx.Response(403), f"{site}/rss": rss("rss_complete.xml")})
    assert discover_feeds(site, fetcher) == [f"{site}/rss"]


def test_extract_declared_feeds_resolves_relative_urls() -> None:
    page = fixture_bytes("site_with_link.html").decode()
    assert extract_declared_feeds(page, SITE) == [
        f"{SITE}/actus/rss.xml",
        f"{SITE}/comments/feed/",
    ]


def test_unreachable_site_stops_early(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    site = "https://down.example"
    route = mock_http.route(host="down.example").mock(side_effect=httpx.ConnectError("x"))
    result = discover_feeds_detailed(site, fetcher)
    assert result.feeds == []
    assert result.homepage_error is not None and "Connexion impossible" in result.homepage_error
    assert result.attempts == []
    assert route.call_count == 6  # robots.txt puis accueil, 3 tentatives chacun
