"""Keenable as the web provider: request shapes, results in Chief's format, fallback to Parallel, keyless by default."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from chief.config import Config
from chief.tools.composio_client import LiveToolError
from chief.tools.keenable import Keenable, KeenableError
from chief.tools.live import LiveTools, _site_filter

SEARCH = {"query": "q", "mode": "pro", "results": [
    {"title": "Boba Guys", "url": "https://bobaguys.example/palo-alto", "description": "short", "snippet": "A long excerpt about Boba Guys."},
    {"title": "", "url": "https://t4.example", "description": "only a description"},
    {"title": "no url"},
]}
PAGE = {"url": "https://www.caltrain.com/schedules/weekday-timetable", "title": "Weekday timetable", "content": "# T\n\n| Zone | Station Name | 102 |"}


def client(handler):
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


def ok(payload):
    return lambda request: httpx.Response(200, json=payload)


def test_keyless_search_uses_the_public_endpoint_and_names_the_app():
    http, calls = client(ok(SEARCH))
    out = Keenable(http=http).search("boba palo alto", 3, site="caltrain.com")
    r = calls[0]
    assert str(r.url) == "https://api.keenable.ai/v1/search/public" and r.headers["X-Keenable-Title"] == "Chief"
    assert "x-api-key" not in r.headers
    assert json.loads(r.content) == {"query": "boba palo alto", "max_results": 3, "mode": "pro", "site": "caltrain.com"}
    assert out == [
        {"title": "Boba Guys", "url": "https://bobaguys.example/palo-alto", "excerpts": ["A long excerpt about Boba Guys."]},
        {"title": "https://t4.example", "url": "https://t4.example", "excerpts": ["only a description"]},  # no title: the url; no snippet: description
    ]  # a result with no url is dropped


def test_a_key_switches_to_the_keyed_endpoint_and_header():
    http, calls = client(ok(SEARCH))
    Keenable("keen_abc123", http=http).search("x")
    assert str(calls[0].url) == "https://api.keenable.ai/v1/search" and calls[0].headers["X-API-Key"] == "keen_abc123"
    assert "x-keenable-title" not in calls[0].headers


def test_fetch_asks_for_the_live_page_and_returns_it_whole():
    http, calls = client(ok(PAGE))
    out = Keenable(http=http).fetch("https://www.caltrain.com/schedules/weekday-timetable", 120_000)
    q = dict(calls[0].url.params)
    assert calls[0].url.path == "/v1/fetch/public" and q["live"] == "true" and q["max_chars"] == "120000" and q["url"].startswith("https://www.caltrain")
    assert out == {"title": "Weekday timetable", "url": PAGE["url"], "excerpts": [PAGE["content"]]}


def test_fetch_reports_the_url_that_was_asked_for_not_the_indexed_one():
    http, _ = client(ok({**PAGE, "url": "https://www.caltrain.com/?active_tab=route_explorer_tab"}))
    assert Keenable(http=http).fetch(PAGE["url"])["url"] == PAGE["url"]


@pytest.mark.parametrize("status,words", [(429, "rate limit"), (402, "credits"), (401, "KEENABLE_API_KEY"), (400, "KEENABLE_API_KEY"), (500, "HTTP 500")])
def test_errors_are_plain_and_never_include_the_key(status, words):
    http, _ = client(lambda r: httpx.Response(status, headers={"Retry-After": "7"}, json={"error": "x"}))
    with pytest.raises(KeenableError) as e:
        Keenable("keen_secret", http=http).search("x")
    assert words in str(e.value) and "keen_secret" not in str(e.value)


def test_network_failure_and_empty_page_are_errors():
    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(KeenableError, match="search failed"):
        Keenable(http=client(boom)[0]).search("x")
    with pytest.raises(KeenableError, match="empty page"):
        Keenable(http=client(ok({"content": ""}))[0]).fetch("https://x.example/")


def test_site_operator_is_lifted_out_of_the_query():
    assert _site_filter("Caltrain schedule to Palo Alto after 6:00 PM site:caltrain.com") == ("Caltrain schedule to Palo Alto after 6:00 PM", "caltrain.com")
    assert _site_filter("511 Caltrain trip site:511.org now") == ("511 Caltrain trip now", "511.org")
    assert _site_filter("plain words") == ("plain words", None)


# ---- through LiveTools


def tools(handler, monkeypatch, provider="keenable", parallel=None, key=None):
    monkeypatch.setenv("CHIEF_WEB_PROVIDER", provider)
    monkeypatch.delenv("CHIEF_SEARCH_DOMAINS", raising=False)
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    http, calls = client(handler)
    return LiveTools(Config(), keenable=Keenable(key, http=http), search_client=parallel, gateway=NS(execute=lambda *a, **k: {})), calls


def test_default_provider_is_parallel_and_never_touches_keenable(monkeypatch):
    class P:
        def search(self, **kw):
            return NS(results=[NS(title="t", url="https://p.example", excerpts=["e"])])

    t, calls = tools(ok(SEARCH), monkeypatch, provider="parallel", parallel=P())
    assert t.search_web("o", ["q"])[0]["url"] == "https://p.example" and calls == []


def test_keenable_search_merges_queries_and_dedupes_by_url(monkeypatch):
    t, calls = tools(ok(SEARCH), monkeypatch)
    out = t.search_web("Find boba", ["boba one", "boba two"], max_results=5)
    assert len(calls) == 2 and [r["url"] for r in out] == ["https://bobaguys.example/palo-alto", "https://t4.example"]


def test_pinned_domains_and_inline_site_become_the_site_filter(monkeypatch):
    t, calls = tools(ok(SEARCH), monkeypatch)
    t.search_web("o", ["Caltrain times site:caltrain.com", "511 trip site:511.org"], 3)
    assert [json.loads(c.content)["site"] for c in calls] == ["caltrain.com", "511.org"]
    assert "site:" not in json.loads(calls[0].content)["query"]
    t2, calls2 = tools(ok(SEARCH), monkeypatch)
    t2.search_web("o", ["plain"], 3, include_domains=["caltrain.com", "511.org"])
    assert [json.loads(c.content)["site"] for c in calls2] == ["caltrain.com", "511.org"]


def test_keenable_failure_falls_back_to_parallel_when_it_is_configured(monkeypatch):
    class P:
        def search(self, **kw):
            return NS(results=[NS(title="t", url="https://p.example", excerpts=["e"])])

        def extract(self, **kw):
            return NS(results=[NS(title="T", url="https://www.caltrain.com/x", excerpts=["a"], full_content="WHOLE")])

    down = lambda r: httpx.Response(503, json={})
    t, _ = tools(down, monkeypatch, parallel=P())
    assert t.search_web("o", ["q"])[0]["url"] == "https://p.example"
    assert t.extract_page("https://www.caltrain.com/x")["excerpts"] == ["WHOLE"]


def test_keenable_failure_without_parallel_is_a_clear_live_tool_error(monkeypatch):
    t, _ = tools(lambda r: httpx.Response(429, json={}), monkeypatch)
    with pytest.raises(LiveToolError, match="rate limit"):
        t.search_web("o", ["q"])
    with pytest.raises(LiveToolError, match="rate limit"):
        t.extract_page("https://www.caltrain.com/x")


def test_extract_page_through_keenable_keeps_the_https_only_rule(monkeypatch):
    t, calls = tools(ok(PAGE), monkeypatch)
    assert t.extract_page("https://www.caltrain.com/schedules/weekday-timetable")["excerpts"] == [PAGE["content"]]
    with pytest.raises(LiveToolError, match="https"):
        t.extract_page("http://www.caltrain.com/x")
    assert len(calls) == 1
