from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from synd.builder.fetch import (
    extract_links,
    fetch_html,
    fetch_page,
    fetch_text,
    html_to_markdown,
)
from synd.errors import FetchError

_CRAWL_FIXTURES = Path(__file__).parent.parent / "fixtures" / "crawl_site"


def _mock_urlopen(
    body: str,
    content_type: str = "text/html",
    final_url: str | None = None,
) -> MagicMock:
    """Build a context-manager mock that returns body bytes from .read()."""
    response = MagicMock()
    response.read.return_value = body.encode("utf-8")
    response.headers.get_content_type.return_value = content_type
    response.geturl.return_value = final_url
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=response)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_fetch_page_md_url_routes_to_mdx_pipeline() -> None:
    body = "# Hello\n\n<Note>Some note.</Note>\n"
    with patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)):
        result = fetch_page("https://example.com/page.md")
    assert "Some note." in result
    assert "<Note>" not in result


def test_fetch_page_html_url_routes_to_html_pipeline() -> None:
    body = "<html><body><main><h1>Hello</h1><p>World</p></main></body></html>"
    with patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)):
        result = fetch_page("https://example.com/page.html")
    assert "Hello" in result
    assert "World" in result
    assert "<html>" not in result


def test_fetch_page_url_without_extension_routes_to_html_pipeline() -> None:
    body = "<html><body><main><p>Content here.</p></main></body></html>"
    with patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)):
        result = fetch_page("https://docs.example.com/guide")
    assert "Content here." in result
    assert "<html>" not in result


def test_fetch_page_raises_fetch_error_on_4xx() -> None:
    import http.client

    headers = http.client.HTTPMessage()
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=HTTPError(
            "https://example.com/page.md", 404, "Not Found", headers, None
        ),
    ):
        with pytest.raises(FetchError, match="HTTP 404"):
            fetch_page("https://example.com/page.md")


def test_fetch_page_raises_fetch_error_on_network_failure() -> None:
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=URLError("connection refused"),
    ):
        with pytest.raises(FetchError, match="Network error"):
            fetch_page("https://example.com/page.html")


def test_fetch_page_rate_limit_sleep_is_called() -> None:
    body = "# Title\n"
    with (
        patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)),
        patch("synd.builder.fetch.time") as mock_time,
    ):
        fetch_page("https://example.com/page.md", rate_limit_sleep=0.5)
    mock_time.sleep.assert_called_once_with(0.5)


def test_fetch_page_no_sleep_when_zero() -> None:
    body = "# Title\n"
    with (
        patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)),
        patch("synd.builder.fetch.time") as mock_time,
    ):
        fetch_page("https://example.com/page.md", rate_limit_sleep=0.0)
    mock_time.sleep.assert_not_called()


# --- fetch_text ---


def test_fetch_text_returns_raw_content() -> None:
    body = "Source: https://example.com/page.md\n# Title\nContent.\n"
    with patch("synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)):
        result = fetch_text("https://example.com/llms-full.txt")
    assert result == body


def test_fetch_text_raises_fetch_error_on_4xx() -> None:
    import http.client

    headers = http.client.HTTPMessage()
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=HTTPError(
            "https://example.com/llms-full.txt", 404, "Not Found", headers, None
        ),
    ):
        with pytest.raises(FetchError, match="HTTP 404"):
            fetch_text("https://example.com/llms-full.txt")


def test_fetch_text_raises_fetch_error_on_network_failure() -> None:
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=URLError("connection refused"),
    ):
        with pytest.raises(FetchError, match="Network error"):
            fetch_text("https://example.com/llms-full.txt")


# --- User-Agent threading ---


def test_fetch_text_sends_default_user_agent() -> None:
    with patch(
        "synd.builder.fetch.urlopen", return_value=_mock_urlopen("body")
    ) as mock_open:
        fetch_text("https://example.com/llms.txt")
    request = mock_open.call_args[0][0]
    assert request.get_header("User-agent", "").startswith("synd/")


def test_fetch_text_sends_custom_user_agent() -> None:
    with patch(
        "synd.builder.fetch.urlopen", return_value=_mock_urlopen("body")
    ) as mock_open:
        fetch_text("https://example.com/llms.txt", user_agent="custom-agent/9.9")
    request = mock_open.call_args[0][0]
    assert request.get_header("User-agent") == "custom-agent/9.9"


def test_fetch_page_threads_custom_user_agent() -> None:
    body = "<html><body><main><p>Hi.</p></main></body></html>"
    with patch(
        "synd.builder.fetch.urlopen", return_value=_mock_urlopen(body)
    ) as mock_open:
        fetch_page("https://example.com/page.html", user_agent="custom-agent/9.9")
    request = mock_open.call_args[0][0]
    assert request.get_header("User-agent") == "custom-agent/9.9"


# --- fetch_html ---


def test_fetch_html_returns_text_content_type_and_final_url() -> None:
    body = "<html><body><main><p>Hello.</p></main></body></html>"
    mock = _mock_urlopen(
        body,
        content_type="text/html",
        final_url="https://docs.example.com/moved/page.html",
    )
    with patch("synd.builder.fetch.urlopen", return_value=mock):
        fetched = fetch_html("https://docs.example.com/page.html")
    assert fetched.url == "https://docs.example.com/page.html"
    assert fetched.final_url == "https://docs.example.com/moved/page.html"
    assert fetched.text == body
    assert fetched.content_type == "text/html"


def test_fetch_html_sends_custom_user_agent() -> None:
    with patch(
        "synd.builder.fetch.urlopen", return_value=_mock_urlopen("<html></html>")
    ) as mock_open:
        fetch_html("https://docs.example.com/", user_agent="custom-agent/9.9")
    request = mock_open.call_args[0][0]
    assert request.get_header("User-agent") == "custom-agent/9.9"


def test_fetch_html_raises_fetch_error_on_4xx() -> None:
    import http.client

    headers = http.client.HTTPMessage()
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=HTTPError("https://example.com/", 403, "Forbidden", headers, None),
    ):
        with pytest.raises(FetchError, match="HTTP 403"):
            fetch_html("https://example.com/")


def test_fetch_html_raises_fetch_error_on_network_failure() -> None:
    with patch(
        "synd.builder.fetch.urlopen",
        side_effect=URLError("connection refused"),
    ):
        with pytest.raises(FetchError, match="Network error"):
            fetch_html("https://example.com/")


# --- extract_links ---


def test_extract_links_resolves_relative_urls() -> None:
    html = '<a href="../api/index.html">API</a> <a href="guide.html">Guide</a>'
    links = extract_links(html, "https://docs.example.com/en/latest/user/")
    assert links == [
        "https://docs.example.com/en/latest/api/index.html",
        "https://docs.example.com/en/latest/user/guide.html",
    ]


def test_extract_links_keeps_absolute_urls() -> None:
    # Scope filtering is the crawler's job; extract_links reports all http(s) links.
    html = '<a href="https://other.example.org/page.html">x</a>'
    links = extract_links(html, "https://docs.example.com/")
    assert links == ["https://other.example.org/page.html"]


def test_extract_links_strips_fragments_and_dedups() -> None:
    html = '<a href="page.html#a">A</a> <a href="page.html#b">B</a>'
    links = extract_links(html, "https://docs.example.com/")
    assert links == ["https://docs.example.com/page.html"]


def test_extract_links_skips_non_http_schemes() -> None:
    html = (
        '<a href="mailto:x@example.com">m</a>'
        '<a href="javascript:void(0)">j</a>'
        '<a href="data:text/plain,hi">d</a>'
        '<a href="tel:+1234">t</a>'
        '<a href="https://docs.example.com/ok.html">ok</a>'
    )
    links = extract_links(html, "https://docs.example.com/")
    assert links == ["https://docs.example.com/ok.html"]


def test_extract_links_preserves_document_order() -> None:
    html = '<a href="b.html">B</a> <a href="a.html">A</a> <a href="b.html">B2</a>'
    links = extract_links(html, "https://docs.example.com/")
    assert links == [
        "https://docs.example.com/b.html",
        "https://docs.example.com/a.html",
    ]


def test_extract_links_empty_html_returns_empty() -> None:
    assert extract_links("", "https://docs.example.com/") == []


# --- html_to_markdown main-content targeting across real doc themes ---


@pytest.mark.parametrize(
    "fixture_name",
    [
        "sphinx_rtd.html",
        "pydata.html",
        "mkdocs_material.html",
        "pallets.html",
        "plain.html",
    ],
)
def test_html_to_markdown_extracts_main_content_per_theme(fixture_name: str) -> None:
    html = (_CRAWL_FIXTURES / fixture_name).read_text(encoding="utf-8")
    md = html_to_markdown(html)
    assert "Unique main content" in md
    assert "NAV-BOILERPLATE" not in md


# --- html_to_markdown in-main boilerplate stripping by CSS class ---


def test_html_to_markdown_strips_sphinx_gallery_furniture() -> None:
    # Sphinx-Gallery example pages carry download notes, footers, and a
    # signature inside <main> — 433 of matplotlib's 1927 chunks were this.
    html = (
        "<html><body><main>"
        '<div class="admonition note sphx-glr-download-link-note">'
        "<p>Go to the end to download the full example code.</p></div>"
        "<h1>Example</h1><p>Real prose.</p>"
        '<div class="sphx-glr-footer sphx-glr-footer-example">'
        '<div class="sphx-glr-download">Download Python source code</div></div>'
        '<p class="sphx-glr-timing">Total running time of the script: (0 minutes '
        "1.234 seconds)</p>"
        '<p class="sphx-glr-signature">'
        '<a href="https://sphinx-gallery.github.io">'
        "Gallery generated by Sphinx-Gallery</a></p>"
        "</main></body></html>"
    )
    md = html_to_markdown(html)
    assert "Real prose." in md
    assert "download the full example code" not in md
    assert "Download Python source code" not in md
    assert "Total running time" not in md
    assert "Gallery generated by Sphinx-Gallery" not in md


def test_html_to_markdown_strips_pydata_secondary_sidebar() -> None:
    # pydata-sphinx-theme places the "On this page" TOC sidebar inside <main>.
    html = (
        "<html><body><main>"
        "<h1>Guide</h1><p>Body text.</p>"
        '<div id="pst-secondary-sidebar" class="bd-sidebar-secondary bd-toc">'
        '<div class="page-toc tocsection onthispage">On this page</div>'
        "</div>"
        "</main></body></html>"
    )
    md = html_to_markdown(html)
    assert "Body text." in md
    assert "On this page" not in md


def test_html_to_markdown_strips_sqlalchemy_sidebar_by_id() -> None:
    # sqlalchemy's theme has no <main>/role="main", so extraction falls back
    # to <body> — which includes the full navigation TOC in
    # <div id="fixed-sidebar">, duplicated into every page (an 11k-token
    # chunk on all 138 crawled pages).
    html = (
        "<html><body>"
        '<div id="docs-top-navigation-container">'
        "<a>Download this Documentation</a></div>"
        '<div id="fixed-sidebar" class="withsidebar">'
        '<div id="docs-sidebar"><ul><li>SQL Statements and Expressions API'
        "</li></ul></div></div>"
        '<div id="narrow-index-nav">On this page:</div>'
        '<div id="docs-body"><h1>Core Internals</h1><p>Actual API docs.</p></div>'
        "</body></html>"
    )
    md = html_to_markdown(html)
    assert "Actual API docs." in md
    assert "SQL Statements and Expressions API" not in md
    assert "Download this Documentation" not in md
    assert "On this page:" not in md


def test_html_to_markdown_keeps_elements_with_unrelated_classes() -> None:
    html = (
        "<html><body><main>"
        '<div class="admonition note"><p>A real note.</p></div>'
        '<p class="highlight">Highlighted prose.</p>'
        "</main></body></html>"
    )
    md = html_to_markdown(html)
    assert "A real note." in md
    assert "Highlighted prose." in md
