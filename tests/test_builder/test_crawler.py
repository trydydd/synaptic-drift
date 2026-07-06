from __future__ import annotations

import http.client
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from synd.builder.crawler import (
    canonicalize_url,
    crawl,
    in_scope,
    parse_sitemap,
)
from synd.errors import BuildError, CrawlError

_ROOT = "https://docs.example.com/en/stable/"
_HOST = "https://docs.example.com"


class _FakeSite:
    """Dispatch mocked urlopen calls by URL; record every request made."""

    def __init__(self, pages: dict[str, str | tuple[str, str]]) -> None:
        self.pages = pages
        self.requested: list[str] = []
        self.user_agents: dict[str, str] = {}

    def urlopen(self, request: object, timeout: int = 30) -> MagicMock:
        url: str = request.full_url  # type: ignore[attr-defined]
        self.requested.append(url)
        ua = request.get_header("User-agent", "")  # type: ignore[attr-defined]
        self.user_agents[url] = ua
        if url not in self.pages:
            raise HTTPError(url, 404, "Not Found", http.client.HTTPMessage(), None)
        entry = self.pages[url]
        if isinstance(entry, str):
            body, content_type = entry, "text/html"
        else:
            body, content_type = entry
        response = MagicMock()
        response.read.return_value = body.encode("utf-8")
        response.headers.get_content_type.return_value = content_type
        response.geturl.return_value = url
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=response)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx


def _page(title: str, links: tuple[str, ...] = ()) -> str:
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return (
        f"<html><body><main><h1>{title}</h1>"
        f"<p>{title} body content.</p>{anchors}</main></body></html>"
    )


def _crawl(site: _FakeSite, root: str = _ROOT, **kwargs: object):
    with (
        patch("synd.builder.fetch.urlopen", site.urlopen),
        patch("synd.builder.crawler.time"),
    ):
        return crawl(root, rate_limit_sleep=0.0, **kwargs)  # type: ignore[arg-type]


# --- canonicalize_url ---


def test_canonicalize_strips_query_and_fragment() -> None:
    url = "https://Docs.Example.com/en/stable/page.html?v=1#section"
    assert canonicalize_url(url) == "https://docs.example.com/en/stable/page.html"


def test_canonicalize_resolves_relative_against_base() -> None:
    assert (
        canonicalize_url("../api/", base_url="https://docs.example.com/en/stable/user/")
        == "https://docs.example.com/en/stable/api/"
    )


def test_canonicalize_folds_index_html_to_directory() -> None:
    assert (
        canonicalize_url("https://docs.example.com/en/stable/foo/index.html")
        == "https://docs.example.com/en/stable/foo/"
    )
    assert (
        canonicalize_url("https://docs.example.com/foo/index.htm")
        == "https://docs.example.com/foo/"
    )


def test_canonicalize_preserves_path_case() -> None:
    assert (
        canonicalize_url("https://docs.example.com/API/Index.html")
        == "https://docs.example.com/API/Index.html"
    )


# --- in_scope ---


def test_in_scope_accepts_pages_under_root_directory() -> None:
    assert in_scope("https://docs.example.com/en/stable/api/index.html", _ROOT)
    assert in_scope("https://docs.example.com/en/stable/", _ROOT)
    assert in_scope("https://docs.example.com/en/stable", _ROOT)


def test_in_scope_rejects_sibling_version_paths() -> None:
    assert not in_scope("https://docs.example.com/en/v1.0/api.html", _ROOT)
    assert not in_scope("https://docs.example.com/en/stablefoo/x.html", _ROOT)
    assert not in_scope("https://docs.example.com/", _ROOT)


def test_in_scope_rejects_other_hosts() -> None:
    assert not in_scope("https://other.example.org/en/stable/api.html", _ROOT)


def test_in_scope_normalizes_root_without_trailing_slash() -> None:
    root = "https://docs.pytest.org/en/stable"
    assert in_scope("https://docs.pytest.org/en/stable/how-to/usage.html", root)
    assert not in_scope("https://docs.pytest.org/en/latest/x.html", root)


def test_in_scope_root_index_html_scopes_to_parent_directory() -> None:
    root = "https://docs.example.com/en/stable/index.html"
    assert in_scope("https://docs.example.com/en/stable/guide.html", root)
    assert not in_scope("https://docs.example.com/en/other/guide.html", root)


# --- parse_sitemap ---


def test_parse_sitemap_urlset_returns_page_urls() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://docs.example.com/en/stable/</loc></url>
      <url><loc>https://docs.example.com/en/stable/api.html</loc></url>
    </urlset>"""
    pages, nested = parse_sitemap(xml)
    assert pages == [
        "https://docs.example.com/en/stable/",
        "https://docs.example.com/en/stable/api.html",
    ]
    assert nested == []


def test_parse_sitemap_index_returns_nested_sitemaps() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://docs.example.com/sitemap-a.xml</loc></sitemap>
      <sitemap><loc>https://docs.example.com/sitemap-b.xml</loc></sitemap>
    </sitemapindex>"""
    pages, nested = parse_sitemap(xml)
    assert pages == []
    assert nested == [
        "https://docs.example.com/sitemap-a.xml",
        "https://docs.example.com/sitemap-b.xml",
    ]


def test_parse_sitemap_without_namespace() -> None:
    xml = "<urlset><url><loc>https://docs.example.com/a.html</loc></url></urlset>"
    pages, nested = parse_sitemap(xml)
    assert pages == ["https://docs.example.com/a.html"]
    assert nested == []


def test_parse_sitemap_malformed_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        parse_sitemap("this is << not xml")


# --- crawl: BFS link-following ---


def test_crawl_bfs_visits_all_in_scope_pages() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("a.html", "sub/b.html")),
            f"{_ROOT}a.html": _page("A"),
            f"{_ROOT}sub/b.html": _page("B", ("../a.html",)),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "links"
    assert sorted(p.url for p in result.pages) == [
        _ROOT,
        f"{_ROOT}a.html",
        f"{_ROOT}sub/b.html",
    ]
    assert all("body content" in p.content for p in result.pages)


def test_crawl_out_of_scope_links_never_requested() -> None:
    site = _FakeSite(
        {
            _ROOT: _page(
                "Index",
                (
                    "a.html",
                    "https://docs.example.com/en/v1.0/old.html",
                    "https://other.example.org/x.html",
                ),
            ),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert "https://docs.example.com/en/v1.0/old.html" not in site.requested
    assert "https://other.example.org/x.html" not in site.requested
    assert result.skipped_out_of_scope == 2


def test_crawl_noise_urls_never_fetched() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("a.html", "genindex.html", "search.html")),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert f"{_ROOT}genindex.html" not in site.requested
    assert f"{_ROOT}search.html" not in site.requested
    assert result.skipped_noise == 2


def test_crawl_asset_extensions_never_fetched() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("a.html", "_images/plot.png", "theme.css")),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert f"{_ROOT}theme.css" not in site.requested
    # _images is also a crawl noise pattern; the .png never gets fetched either way
    assert f"{_ROOT}_images/plot.png" not in site.requested
    assert result.skipped_noise == 2


def test_crawl_dedups_index_html_and_trailing_slash_variants() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("a/index.html", "a/", "a")),
            f"{_ROOT}a/": _page("A"),
        }
    )
    result = _crawl(site)
    a_requests = [u for u in site.requested if u.rstrip("/").endswith("/a")]
    assert a_requests == [f"{_ROOT}a/"]
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}a/"]


def test_crawl_skips_non_html_content_type() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("download",)),
            f"{_ROOT}download": ("%PDF-1.4 fake", "application/pdf"),
        }
    )
    result = _crawl(site)
    assert result.skipped_non_html == 1
    assert [p.url for p in result.pages] == [_ROOT]


def test_crawl_max_pages_truncates_with_flag() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("a.html", "b.html", "c.html")),
            f"{_ROOT}a.html": _page("A"),
            f"{_ROOT}b.html": _page("B"),
            f"{_ROOT}c.html": _page("C"),
        }
    )
    result = _crawl(site, max_pages=2)
    assert len(result.pages) == 2
    assert result.truncated is True


def test_crawl_not_truncated_when_under_max_pages() -> None:
    site = _FakeSite({_ROOT: _page("Index")})
    result = _crawl(site, max_pages=10)
    assert result.truncated is False


def test_crawl_zero_pages_raises_crawl_error() -> None:
    # Root fetches fine but is not HTML → nothing chunkable anywhere.
    site = _FakeSite({_ROOT: ("binary", "application/octet-stream")})
    with pytest.raises(CrawlError, match="No documentation pages"):
        _crawl(site)


def test_crawl_root_unreachable_raises_crawl_error() -> None:
    site = _FakeSite({})
    with pytest.raises(CrawlError, match="root"):
        _crawl(site)


def test_crawl_error_is_a_build_error() -> None:
    assert issubclass(CrawlError, BuildError)


def test_crawl_page_fetch_failure_skips_and_continues() -> None:
    site = _FakeSite(
        {
            _ROOT: _page("Index", ("missing.html", "a.html")),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}a.html"]


# --- crawl: robots.txt ---


def _robots(body: str) -> dict[str, str | tuple[str, str]]:
    return {f"{_HOST}/robots.txt": (body, "text/plain")}


def test_crawl_respects_robots_disallow_page() -> None:
    site = _FakeSite(
        {
            **_robots("User-agent: *\nDisallow: /en/stable/private"),
            _ROOT: _page("Index", ("private.html", "public.html")),
            f"{_ROOT}public.html": _page("Public"),
        }
    )
    result = _crawl(site)
    assert f"{_ROOT}private.html" not in site.requested
    assert result.skipped_robots == 1
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}public.html"]


def test_crawl_root_disallowed_raises_crawl_error() -> None:
    site = _FakeSite(
        {
            **_robots("User-agent: *\nDisallow: /en/stable/"),
            _ROOT: _page("Index"),
        }
    )
    with pytest.raises(CrawlError, match="robots.txt"):
        _crawl(site)


def test_crawl_robots_unreachable_allows_all() -> None:
    site = _FakeSite({_ROOT: _page("Index")})  # no robots.txt → 404
    result = _crawl(site)
    assert [p.url for p in result.pages] == [_ROOT]


def test_crawl_no_robots_bypasses_robots_entirely() -> None:
    site = _FakeSite(
        {
            **_robots("User-agent: *\nDisallow: /"),
            _ROOT: _page("Index", ("private.html",)),
            f"{_ROOT}private.html": _page("Private"),
        }
    )
    result = _crawl(site, respect_robots=False)
    assert f"{_HOST}/robots.txt" not in site.requested
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}private.html"]


def test_crawl_honors_crawl_delay_over_rate_limit() -> None:
    site = _FakeSite(
        {
            **_robots("User-agent: *\nCrawl-delay: 2"),
            _ROOT: _page("Index"),
        }
    )
    with (
        patch("synd.builder.fetch.urlopen", site.urlopen),
        patch("synd.builder.crawler.time") as mock_time,
    ):
        crawl(_ROOT, rate_limit_sleep=0.5)
    mock_time.sleep.assert_called_with(2.0)


def test_crawl_uses_custom_user_agent_for_robots_and_pages() -> None:
    site = _FakeSite(
        {
            **_robots("User-agent: *\nDisallow:"),
            _ROOT: _page("Index"),
        }
    )
    _crawl(site, user_agent="my-crawler/1.0")
    assert site.user_agents[f"{_HOST}/robots.txt"] == "my-crawler/1.0"
    assert site.user_agents[_ROOT] == "my-crawler/1.0"


# --- crawl: sitemap discovery ---


def _urlset(*urls: str) -> str:
    entries = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


def test_crawl_prefers_sitemap_and_skips_link_following() -> None:
    site = _FakeSite(
        {
            **_robots(f"User-agent: *\nSitemap: {_ROOT}sitemap.xml"),
            f"{_ROOT}sitemap.xml": (
                _urlset(_ROOT, f"{_ROOT}a.html"),
                "application/xml",
            ),
            _ROOT: _page("Index", ("hidden.html",)),
            f"{_ROOT}a.html": _page("A"),
            f"{_ROOT}hidden.html": _page("Hidden"),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "sitemap"
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}a.html"]
    # linked-but-not-in-sitemap page is never requested in sitemap mode
    assert f"{_ROOT}hidden.html" not in site.requested


def test_crawl_falls_back_to_root_sitemap_xml_without_robots_directive() -> None:
    site = _FakeSite(
        {
            f"{_ROOT}sitemap.xml": (_urlset(f"{_ROOT}a.html"), "application/xml"),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "sitemap"
    assert [p.url for p in result.pages] == [f"{_ROOT}a.html"]


def test_crawl_sitemap_index_recurses_into_nested_sitemaps() -> None:
    index_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{_ROOT}sitemap-a.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    site = _FakeSite(
        {
            f"{_ROOT}sitemap.xml": (index_xml, "application/xml"),
            f"{_ROOT}sitemap-a.xml": (_urlset(f"{_ROOT}a.html"), "application/xml"),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "sitemap"
    assert [p.url for p in result.pages] == [f"{_ROOT}a.html"]


def test_crawl_malformed_sitemap_falls_back_to_bfs() -> None:
    site = _FakeSite(
        {
            f"{_ROOT}sitemap.xml": ("this is << not xml", "application/xml"),
            _ROOT: _page("Index", ("a.html",)),
            f"{_ROOT}a.html": _page("A"),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "links"
    assert sorted(p.url for p in result.pages) == [_ROOT, f"{_ROOT}a.html"]


def test_crawl_sitemap_with_only_out_of_scope_urls_falls_back_to_bfs() -> None:
    site = _FakeSite(
        {
            f"{_ROOT}sitemap.xml": (
                _urlset("https://docs.example.com/en/v1.0/old.html"),
                "application/xml",
            ),
            _ROOT: _page("Index"),
        }
    )
    result = _crawl(site)
    assert result.discovered_via == "links"
    assert [p.url for p in result.pages] == [_ROOT]


def test_crawl_result_reports_root_url() -> None:
    site = _FakeSite({_ROOT: _page("Index")})
    result = _crawl(site)
    assert result.root_url == _ROOT
