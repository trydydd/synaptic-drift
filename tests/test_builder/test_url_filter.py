from __future__ import annotations

from synd.builder.url_filter import (
    DEFAULT_CRAWL_NOISE_URL_PATTERNS,
    DEFAULT_NOISE_URL_PATTERNS,
    filter_page_urls,
    is_asset_url,
    is_noise_url,
)


class TestIsNoiseUrl:
    def test_exact_segment_match(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/changelog", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_segment_in_path(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/docs/changelog", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_segment_with_subpath(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/releases/v2", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_no_false_positive_on_partial_match(self) -> None:
        # "configuration-updates" must NOT match "updates"
        assert not is_noise_url(
            "https://docs.example.com/configuration-updates", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_no_false_positive_on_unrelated_path(self) -> None:
        assert not is_noise_url(
            "https://docs.example.com/docs/api/auth", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_case_insensitive(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/Changelog", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_updates_segment(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/updates", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_whats_new_segment(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/whats-new", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_empty_patterns_always_false(self) -> None:
        assert not is_noise_url("https://docs.example.com/changelog", ())

    def test_custom_pattern(self) -> None:
        assert is_noise_url("https://docs.example.com/blog", ("blog",))

    def test_root_path_not_noise(self) -> None:
        assert not is_noise_url("https://docs.example.com/", DEFAULT_NOISE_URL_PATTERNS)


class TestFilterPageUrls:
    def test_filters_noise_urls(self) -> None:
        pairs = [
            ("https://docs.example.com/api/auth", "auth content"),
            ("https://docs.example.com/changelog", "v1.0 released"),
            ("https://docs.example.com/guide", "guide content"),
            ("https://docs.example.com/releases/v2", "v2 release notes"),
        ]
        kept, excluded = filter_page_urls(pairs, DEFAULT_NOISE_URL_PATTERNS)
        kept_urls = [u for u, _ in kept]
        assert "https://docs.example.com/api/auth" in kept_urls
        assert "https://docs.example.com/guide" in kept_urls
        assert "https://docs.example.com/changelog" in excluded
        assert "https://docs.example.com/releases/v2" in excluded

    def test_empty_patterns_keeps_all(self) -> None:
        pairs = [
            ("https://docs.example.com/changelog", "content"),
            ("https://docs.example.com/updates", "content"),
        ]
        kept, excluded = filter_page_urls(pairs, ())
        assert len(kept) == 2
        assert excluded == []

    def test_all_kept_when_no_matches(self) -> None:
        pairs = [
            ("https://docs.example.com/api", "api content"),
            ("https://docs.example.com/guide", "guide content"),
        ]
        kept, excluded = filter_page_urls(pairs, DEFAULT_NOISE_URL_PATTERNS)
        assert len(kept) == 2
        assert excluded == []

    def test_empty_input(self) -> None:
        kept, excluded = filter_page_urls([], DEFAULT_NOISE_URL_PATTERNS)
        assert kept == []
        assert excluded == []

    def test_content_preserved_in_kept(self) -> None:
        pairs = [("https://docs.example.com/api", "my content")]
        kept, _ = filter_page_urls(pairs, DEFAULT_NOISE_URL_PATTERNS)
        assert kept[0][1] == "my content"

    def test_all_noise_returns_empty_kept(self) -> None:
        pairs = [
            ("https://docs.example.com/changelog", "c1"),
            ("https://docs.example.com/releases", "c2"),
        ]
        kept, excluded = filter_page_urls(pairs, DEFAULT_NOISE_URL_PATTERNS)
        assert kept == []
        assert len(excluded) == 2


class TestSegmentStemMatching:
    """Noise patterns must also match segments that carry a file extension —
    crawled sites expose /genindex.html where wget mirrors had genindex/."""

    def test_segment_with_html_extension_matches(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/en/latest/genindex.html",
            ("genindex",),
        )

    def test_segment_with_md_extension_matches(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/changelog.md", DEFAULT_NOISE_URL_PATTERNS
        )

    def test_stem_does_not_create_partial_false_positive(self) -> None:
        # "configuration-updates.md" stem is "configuration-updates", not "updates"
        assert not is_noise_url(
            "https://docs.example.com/configuration-updates.md",
            DEFAULT_NOISE_URL_PATTERNS,
        )

    def test_research_does_not_match_search(self) -> None:
        assert not is_noise_url(
            "https://docs.example.com/research.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )


class TestCrawlNoisePatterns:
    def test_includes_all_base_noise_patterns(self) -> None:
        assert set(DEFAULT_NOISE_URL_PATTERNS) <= set(DEFAULT_CRAWL_NOISE_URL_PATTERNS)

    def test_genindex_page_is_noise(self) -> None:
        assert is_noise_url(
            "https://requests.readthedocs.io/en/latest/genindex.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )

    def test_modules_source_viewer_is_noise(self) -> None:
        assert is_noise_url(
            "https://requests.readthedocs.io/en/latest/_modules/requests/models.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )

    def test_search_page_is_noise(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/en/stable/search.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )

    def test_sources_and_static_are_noise(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/_sources/index.rst.txt",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )
        assert is_noise_url(
            "https://docs.example.com/_static/css/theme.css",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )

    def test_py_modindex_is_noise(self) -> None:
        assert is_noise_url(
            "https://docs.example.com/py-modindex.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )

    def test_regular_docs_page_is_not_noise(self) -> None:
        assert not is_noise_url(
            "https://docs.example.com/en/stable/api/index.html",
            DEFAULT_CRAWL_NOISE_URL_PATTERNS,
        )


class TestIsAssetUrl:
    def test_image_and_font_extensions_are_assets(self) -> None:
        for url in (
            "https://docs.example.com/_images/plot.png",
            "https://docs.example.com/logo.svg",
            "https://docs.example.com/fonts/lato.woff2",
        ):
            assert is_asset_url(url), url

    def test_stylesheet_and_script_are_assets(self) -> None:
        assert is_asset_url("https://docs.example.com/css/theme.css")
        assert is_asset_url("https://docs.example.com/js/main.js")

    def test_query_string_does_not_hide_extension(self) -> None:
        assert is_asset_url("https://docs.example.com/css/theme.css?v=2.0")

    def test_archives_and_pdf_are_assets(self) -> None:
        assert is_asset_url("https://docs.example.com/download/docs.pdf")
        assert is_asset_url("https://docs.example.com/release.tar.gz")
        assert is_asset_url("https://docs.example.com/pkg.whl")

    def test_objects_inv_is_asset(self) -> None:
        assert is_asset_url("https://docs.example.com/en/stable/objects.inv")

    def test_plain_text_is_asset(self) -> None:
        # Sphinx _sources pages; llms.txt never reaches the crawler path.
        assert is_asset_url("https://docs.example.com/index.rst.txt")

    def test_html_and_md_and_extensionless_are_not_assets(self) -> None:
        assert not is_asset_url("https://docs.example.com/guide.html")
        assert not is_asset_url("https://docs.example.com/guide.md")
        assert not is_asset_url("https://docs.example.com/guide/")
        assert not is_asset_url("https://docs.example.com/guide")

    def test_case_insensitive_extension(self) -> None:
        assert is_asset_url("https://docs.example.com/IMAGE.PNG")
