from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# URL path segments that signal non-documentation noise pages.
# Matched case-insensitively against individual path segments so that
# "/docs/changelog" matches but "/docs/configuration-updates" does not.
DEFAULT_NOISE_URL_PATTERNS: tuple[str, ...] = (
    "changelog",
    "changelogs",
    "releases",
    "release-notes",
    "updates",
    "news",
    "history",
    "whats-new",
    "what-is-new",
)

# Additional noise for crawled builds: Sphinx/ReadTheDocs generated pages
# (symbol indexes, search UI, raw-source viewers, theme assets). These encode
# exactly what scripts/build-pack-html.sh pruned from wget mirrors manually.
# llms.txt builds keep DEFAULT_NOISE_URL_PATTERNS — index files are curated,
# so the extra patterns are crawl-only.
DEFAULT_CRAWL_NOISE_URL_PATTERNS: tuple[str, ...] = DEFAULT_NOISE_URL_PATTERNS + (
    "genindex",
    "py-modindex",
    "modindex",
    "search",
    "_modules",
    "_sources",
    "_static",
    "_downloads",
    "_images",
)

# File extensions that are never documentation pages. .md is deliberately
# absent (routed through the MDX pipeline); .txt is included because plain
# text pages on crawled sites are almost always Sphinx _sources dumps —
# llms.txt index files never reach the crawler path.
_ASSET_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".css",
        ".js",
        ".mjs",
        ".json",
        ".xml",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".whl",
        ".pdf",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp4",
        ".webm",
        ".inv",
        ".txt",
    }
)


def is_noise_url(url: str, patterns: tuple[str, ...]) -> bool:
    """Return True if any path segment of url matches a noise pattern.

    Matching is against individual path segments (split on '/'),
    case-insensitive. A segment's final extension is also stripped for
    matching so "genindex" matches both ".../genindex/" (wget mirrors) and
    ".../genindex.html" (live sites). Partial matches never fire: the
    pattern "updates" matches ".../updates" and ".../updates/v2" but not
    ".../configuration-updates.md".
    """
    if not patterns:
        return False
    path = urlparse(url).path.lower().strip("/")
    segments = set(path.split("/"))
    stems = {s.rsplit(".", 1)[0] for s in segments}
    lowered = {p.lower().strip("/") for p in patterns}
    return bool((segments | stems) & lowered)


def is_asset_url(url: str) -> bool:
    """Return True if the URL points at a non-page asset (image, CSS, archive…).

    Used by the crawler to skip fetches that can never yield documentation
    content. Matches on the final path segment's extension, case-insensitive,
    ignoring any query string. objects.inv (Sphinx inventory) is always an
    asset.
    """
    path = urlparse(url).path.lower()
    name = path.rsplit("/", 1)[-1]
    if name == "objects.inv":
        return True
    dot = name.rfind(".")
    if dot == -1:
        return False
    return name[dot:] in _ASSET_EXTENSIONS


def filter_page_urls(
    page_pairs: list[tuple[str, str]],
    patterns: tuple[str, ...] = DEFAULT_NOISE_URL_PATTERNS,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Split page_pairs into (kept, excluded_urls) based on noise patterns.

    Returns the kept pairs and a list of excluded URLs for logging.
    Pass patterns=() to disable all filtering.
    """
    if not patterns:
        return list(page_pairs), []

    kept: list[tuple[str, str]] = []
    excluded: list[str] = []

    for url, content in page_pairs:
        if is_noise_url(url, patterns):
            logger.debug("url_filter: excluded %s (matches noise pattern)", url)
            excluded.append(url)
        else:
            kept.append((url, content))

    return kept, excluded
