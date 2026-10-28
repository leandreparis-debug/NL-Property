"""Tests du client HTTP poli."""

from __future__ import annotations

import httpx
import respx

from news_property.http_client import (
    USER_AGENT,
    Fetcher,
    create_client,
    describe_status,
)
from tests.helpers import serve

URL = "https://site.example/page"


def test_client_configuration() -> None:
    with create_client() as client:
        assert client.headers["User-Agent"] == USER_AGENT
        assert USER_AGENT.startswith("NewsProperty/") and "(veille interne)" in USER_AGENT
        assert client.timeout.read == 15.0
        assert client.follow_redirects is True


def test_retry_after_503_then_success(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    routes = serve(mock_http, {URL: [httpx.Response(503), httpx.Response(200, text="ok")]})
    result = fetcher.fetch(URL)
    assert result.ok
    assert result.text == "ok"
    assert routes[URL].call_count == 2
    assert routes[URL].calls[0].request.headers["User-Agent"] == USER_AGENT


def test_gives_up_after_two_retries(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    routes = serve(mock_http, {URL: httpx.Response(502)})
    result = fetcher.fetch(URL)
    assert routes[URL].call_count == 3
    assert not result.ok
    assert result.status_code == 502
    assert "HTTP 502" in (result.error or "")


def test_timeout_becomes_french_error(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    routes = serve(mock_http, {URL: httpx.ConnectTimeout("trop long")})
    result = fetcher.fetch(URL)
    assert routes[URL].call_count == 3
    assert not result.ok
    assert result.status_code is None
    assert result.error is not None and "pas répondu à temps" in result.error


def test_403_is_not_retried(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    routes = serve(mock_http, {URL: httpx.Response(403)})
    result = fetcher.fetch(URL)
    assert routes[URL].call_count == 1
    assert result.error == "Le site a refusé l'accès (HTTP 403)."


def test_page_forbidden_by_robots_is_not_fetched(
    mock_http: respx.MockRouter, fetcher: Fetcher
) -> None:
    private = "https://site.example/prive/page"
    routes = serve(
        mock_http,
        {
            "https://site.example/robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /prive/\n"
            ),
            private: httpx.Response(200, text="secret"),
            URL: httpx.Response(200, text="public"),
        },
    )
    blocked = fetcher.fetch(private, check_robots=True)
    allowed = fetcher.fetch(URL, check_robots=True)

    assert blocked.blocked_by_robots and not blocked.ok
    assert "robots.txt" in (blocked.error or "")
    assert routes[private].call_count == 0
    assert allowed.ok
    assert routes["https://site.example/robots.txt"].call_count == 1  # mis en cache


def test_missing_robots_allows_everything(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    serve(mock_http, {URL: httpx.Response(200, text="ok")})  # robots.txt → 404 (repli)
    assert fetcher.fetch(URL, check_robots=True).ok


def test_rate_limit_per_domain(mock_http: respx.MockRouter) -> None:
    serve(mock_http, {URL: httpx.Response(200), "https://autre.example/": httpx.Response(200)})
    sleeps: list[float] = []
    with Fetcher(sleep=sleeps.append, clock=lambda: 100.0) as fetcher:
        fetcher.fetch(URL)
        fetcher.fetch("https://autre.example/")  # autre domaine : pas d'attente
        assert sleeps == []
        fetcher.fetch(URL)  # même domaine, même instant : attente d'1 s
        assert sleeps == [1.0]


def test_redirect_final_url(mock_http: respx.MockRouter, fetcher: Fetcher) -> None:
    target = "https://site.example/arrivee"
    serve(
        mock_http,
        {
            URL: httpx.Response(301, headers={"location": target}),
            target: httpx.Response(200, text="arrivé"),
        },
    )
    result = fetcher.fetch(URL)
    assert result.ok and result.final_url == target and result.url == URL


def test_describe_status() -> None:
    assert describe_status(404) == "Page introuvable (HTTP 404)."
    assert "HTTP 500" in describe_status(500)
