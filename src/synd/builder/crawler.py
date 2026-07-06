"""General web crawler for documentation sites without llms.txt.

Discovers pages sitemap-first (robots.txt ``Sitemap:`` directives, then
``<root>/sitemap.xml``, then ``<host>/sitemap.xml``), falling back to BFS
link-following from the root URL. Scope is confined to the root's host and
directory path (the wget ``--no-parent`` equivalent). robots.txt is honored
by default via urllib.robotparser; static HTML only, no JS rendering.

The result's page order is discovery order — callers that assign chunk IDs
must sort pages by canonical URL first (see build_pack_from_url), because
sitemap regeneration and link-order changes make discovery order
non-deterministic across runs.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from synd.builder.fetch import (
    _USER_AGENT,
    extract_links,
    fetch_html,
    fetch_text,
    html_to_markdown,
)
from synd.builder.url_filter import (
    DEFAULT_CRAWL_NOISE_URL_PATTERNS,
    is_asset_url,
    is_noise_url,
)
from synd.errors import CrawlError, FetchError

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 500

_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# Safety cap on nested sitemap fetches (a sitemapindex of sitemapindexes
# could otherwise loop or balloon the request count before crawling starts).
_MAX_SITEMAP_FETCHES = 100


@dataclass
class CrawledPage:
    """One successfully fetched and converted documentation page."""

    url: str  # canonical full https:// URL (post-redirect)
    content: str  # markdown from html_to_markdown()


@dataclass
class CrawlResult:
    """Outcome of a crawl: pages in discovery order plus skip accounting."""

    pages: list[CrawledPage]
    root_url: str
    discovered_via: str  # "sitemap" | "links"
    truncated: bool  # hit max_pages with fetchable pages remaining
    skipped_robots: int
    skipped_noise: int
    skipped_out_of_scope: int
    skipped_non_html: int


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    """Normalize a URL to its canonical crawl form.

    Resolves relative references against base_url when given, strips the
    fragment and query string, folds a trailing /index.html or /index.htm
    into the parent directory, and lowercases scheme and host. Path case is
    preserved (paths are case-sensitive).
    """
    if base_url is not None:
        url = urljoin(base_url, url)
    url, _fragment = urldefrag(url)
    parts = urlsplit(url)
    path = parts.path
    for index_name in ("/index.html", "/index.htm"):
        if path.endswith(index_name):
            path = path[: -len(index_name)] + "/"
            break
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _dedup_key(canonical_url: str) -> str:
    """Collapse trailing-slash variants so /foo, /foo/ and /foo/index.html
    (already folded by canonicalize_url) map to one frontier entry."""
    return canonical_url.rstrip("/")


def _scope_dir(path: str) -> str:
    """Return the directory prefix a root path confines the crawl to.

    A path ending in '/' is already a directory; a final segment containing
    a dot is treated as a file (scope is its parent); anything else is
    treated as a directory missing its trailing slash.
    """
    if not path or path == "/":
        return "/"
    if path.endswith("/"):
        return path
    last_segment = path.rsplit("/", 1)[-1]
    if "." in last_segment:
        return path[: path.rfind("/") + 1]
    return path + "/"


def in_scope(url: str, root_url: str) -> bool:
    """Return True if url is on the root's host and under its directory path.

    The wget --no-parent equivalent: same host (case-insensitive) and the
    path must start with the root's directory prefix. http and https are
    treated as interchangeable so a protocol-relative or legacy http link to
    the same host+path is not lost.
    """
    root = urlsplit(canonicalize_url(root_url))
    target = urlsplit(canonicalize_url(url))
    if target.scheme not in ("http", "https"):
        return False
    if target.netloc.lower() != root.netloc.lower():
        return False
    scope = _scope_dir(root.path)
    path = target.path or "/"
    return path.startswith(scope) or path == scope.rstrip("/")


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Parse a sitemap document into (page_urls, nested_sitemap_urls).

    Handles both <urlset> and <sitemapindex> roots, with or without the
    sitemaps.org namespace. Raises xml.etree.ElementTree.ParseError on
    malformed XML — callers fall back to link-following.
    """
    root = ET.fromstring(xml_text)  # noqa: S314 — sitemap XML from the crawled site

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    page_urls: list[str] = []
    nested_sitemaps: list[str] = []
    is_index = local_name(root.tag) == "sitemapindex"
    for entry in root:
        if local_name(entry.tag) not in ("url", "sitemap"):
            continue
        loc = next(
            (
                el.text.strip()
                for el in entry
                if local_name(el.tag) == "loc" and el.text and el.text.strip()
            ),
            None,
        )
        if loc is None:
            continue
        if is_index:
            nested_sitemaps.append(loc)
        else:
            page_urls.append(loc)
    return page_urls, nested_sitemaps


class _RobotsGate:
    """Per-host robots.txt cache with UA-aware allow/crawl-delay/sitemap reads.

    robots.txt is fetched with the crawl's own User-Agent (RobotFileParser's
    .read() would use Python's default UA, which some docs hosts 403). An
    unreachable robots.txt (404, network error) means allow-all, per
    convention. When disabled (--no-robots), robots.txt is never fetched.
    """

    def __init__(self, user_agent: str, enabled: bool = True) -> None:
        self._user_agent = user_agent
        self._enabled = enabled
        # None sentinel: robots.txt unreachable for that host → allow-all.
        self._parsers: dict[str, RobotFileParser | None] = {}

    def _parser_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        netloc = parts.netloc.lower()
        if netloc not in self._parsers:
            robots_url = f"{parts.scheme}://{netloc}/robots.txt"
            try:
                text = fetch_text(robots_url, user_agent=self._user_agent)
            except FetchError:
                logger.info(
                    "crawler: robots.txt unreachable at %s — allowing all", robots_url
                )
                self._parsers[netloc] = None
            else:
                parser = RobotFileParser()
                parser.parse(text.splitlines())
                self._parsers[netloc] = parser
        return self._parsers[netloc]

    def allows(self, url: str) -> bool:
        if not self._enabled:
            return True
        parser = self._parser_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float:
        if not self._enabled:
            return 0.0
        parser = self._parser_for(url)
        if parser is None:
            return 0.0
        delay = parser.crawl_delay(self._user_agent)
        return float(delay) if delay else 0.0

    def sitemap_urls(self, url: str) -> list[str]:
        if not self._enabled:
            return []
        parser = self._parser_for(url)
        if parser is None:
            return []
        return list(parser.site_maps() or [])


def _collect_sitemap_pages(sitemap_sources: list[str], user_agent: str) -> list[str]:
    """Fetch a set of sitemaps (recursing into sitemap indexes) and return
    every listed page URL. Fetch/parse failures skip that sitemap."""
    page_urls: list[str] = []
    to_fetch: deque[str] = deque(sitemap_sources)
    fetched: set[str] = set()
    while to_fetch and len(fetched) < _MAX_SITEMAP_FETCHES:
        sitemap_url = to_fetch.popleft()
        if sitemap_url in fetched:
            continue
        fetched.add(sitemap_url)
        try:
            xml_text = fetch_text(sitemap_url, user_agent=user_agent)
        except FetchError as exc:
            logger.debug("crawler: sitemap fetch failed %s: %s", sitemap_url, exc)
            continue
        try:
            pages, nested = parse_sitemap(xml_text)
        except ET.ParseError as exc:
            logger.warning("crawler: malformed sitemap %s: %s", sitemap_url, exc)
            continue
        page_urls.extend(pages)
        to_fetch.extend(nested)
    return page_urls


def _discover_via_sitemaps(root: str, gate: _RobotsGate, user_agent: str) -> list[str]:
    """Try sitemap sources in preference order; return the first non-empty
    page list. Order: robots.txt directives, <root-dir>/sitemap.xml,
    <host>/sitemap.xml."""
    parts = urlsplit(root)
    root_dir = _scope_dir(parts.path)
    root_dir_sitemap = urlunsplit(
        (parts.scheme, parts.netloc, root_dir + "sitemap.xml", "", "")
    )
    host_sitemap = urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))

    candidate_sets: list[list[str]] = []
    robots_directives = gate.sitemap_urls(root)
    if robots_directives:
        candidate_sets.append(robots_directives)
    candidate_sets.append([root_dir_sitemap])
    if host_sitemap != root_dir_sitemap:
        candidate_sets.append([host_sitemap])

    for sources in candidate_sets:
        pages = _collect_sitemap_pages(sources, user_agent)
        if pages:
            return pages
    return []


def crawl(
    root_url: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    rate_limit_sleep: float = 0.5,
    user_agent: str = _USER_AGENT,
    respect_robots: bool = True,
    excluded_url_patterns: tuple[str, ...] = DEFAULT_CRAWL_NOISE_URL_PATTERNS,
) -> CrawlResult:
    """Crawl a documentation site root and return converted markdown pages.

    Raises CrawlError when the crawl as a whole is impossible or empty:
    robots.txt disallows the root, the root is unreachable in link-following
    mode, or zero usable pages remain after filtering. Individual page
    failures are skipped with a warning.
    """
    root = canonicalize_url(root_url)
    gate = _RobotsGate(user_agent=user_agent, enabled=respect_robots)

    if not gate.allows(root):
        raise CrawlError(
            f"robots.txt disallows crawling {root} for User-Agent {user_agent!r} "
            "(pass --no-robots to bypass)"
        )

    sitemap_pages = [
        canonicalize_url(u) for u in _discover_via_sitemaps(root, gate, user_agent)
    ]
    in_scope_sitemap_pages = [u for u in sitemap_pages if in_scope(u, root)]

    frontier: deque[str]
    if in_scope_sitemap_pages:
        discovered_via = "sitemap"
        follow_links = False
        frontier = deque(sitemap_pages)
    else:
        discovered_via = "links"
        follow_links = True
        frontier = deque([root])

    delay = max(rate_limit_sleep, gate.crawl_delay(root))
    seen: set[str] = set()
    pages: list[CrawledPage] = []
    truncated = False
    skipped_robots = 0
    skipped_noise = 0
    skipped_out_of_scope = 0
    skipped_non_html = 0

    while frontier:
        url = frontier.popleft()
        key = _dedup_key(url)
        if key in seen:
            continue
        seen.add(key)
        if not in_scope(url, root):
            skipped_out_of_scope += 1
            continue
        if is_noise_url(url, excluded_url_patterns) or is_asset_url(url):
            skipped_noise += 1
            logger.debug("crawler: skipped noise/asset URL %s", url)
            continue
        if not gate.allows(url):
            skipped_robots += 1
            logger.debug("crawler: robots.txt disallows %s", url)
            continue
        if len(pages) >= max_pages:
            truncated = True
            break

        try:
            fetched = fetch_html(url, user_agent=user_agent)
        except FetchError as exc:
            if follow_links and url == root and not pages:
                raise CrawlError(f"Failed to fetch crawl root {url}: {exc}") from exc
            logger.warning("crawler: skipping %s: %s", url, exc)
            continue
        if delay > 0:
            time.sleep(delay)

        final = canonicalize_url(fetched.final_url)
        if final != url:
            final_key = _dedup_key(final)
            if final_key in seen:
                continue
            seen.add(final_key)
            if not in_scope(final, root):
                skipped_out_of_scope += 1
                logger.debug("crawler: redirect left scope %s -> %s", url, final)
                continue

        if fetched.content_type not in _HTML_CONTENT_TYPES:
            skipped_non_html += 1
            logger.debug(
                "crawler: skipped non-HTML %s (%s)", final, fetched.content_type
            )
            continue

        content = html_to_markdown(fetched.text)
        if not content:
            logger.debug("crawler: skipped empty page %s", final)
            continue
        pages.append(CrawledPage(url=final, content=content))

        if follow_links:
            for link in extract_links(fetched.text, base_url=fetched.final_url):
                frontier.append(canonicalize_url(link))

    if not pages:
        raise CrawlError(
            f"No documentation pages found crawling {root_url} — check that the "
            "URL is a docs root and robots.txt allows access"
        )

    return CrawlResult(
        pages=pages,
        root_url=root_url,
        discovered_via=discovered_via,
        truncated=truncated,
        skipped_robots=skipped_robots,
        skipped_noise=skipped_noise,
        skipped_out_of_scope=skipped_out_of_scope,
        skipped_non_html=skipped_non_html,
    )
