"""synd build command."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from synd.builder.build import build_pack, build_pack_from_url
from synd.builder.chunking import (
    RawChunk,
    _DEFAULT_MAX_CHUNK_TOKENS,
    _DEFAULT_MIN_CHUNK_TOKENS,
)
from synd.builder.crawler import DEFAULT_MAX_PAGES
from synd.builder.manifest import load_manifest
from synd.builder.summarize import LlmSummarizerConfig
from synd.cli.exit_codes import EXIT_USAGE, exit_code_for
from synd.errors import BuildError, SyndError

console = Console()


def _parse_package_spec(spec: str) -> tuple[str, str]:
    """Parse 'package@version' string.

    Returns (package, version).
    Raises BuildError on invalid format.
    """
    if "@" not in spec:
        raise BuildError(
            f"Missing '@' in package spec '{spec}'. "
            "Expected format: package@version (e.g. my-lib@1.0.0)"
        )
    parts = spec.split("@")
    if len(parts) != 2:
        raise BuildError(
            f"Invalid package spec '{spec}'. "
            "Multiple '@' signs detected. Expected format: package@version"
        )
    pkg, ver = parts[0], parts[1]
    if not pkg or not ver:
        raise BuildError(
            f"Invalid package spec '{spec}'. "
            "Package name and version must be non-empty."
        )
    return pkg, ver


@click.command()
@click.argument("package_spec")
@click.option(
    "--source",
    required=True,
    type=str,
    help=(
        "Local directory, llms(-full).txt URL, or docs-site root URL "
        "(crawled when it does not end in llms.txt/llms-full.txt)"
    ),
)
@click.option("--output", type=click.Path(path_type=Path), default=Path("."))
@click.option(
    "--lifecycle", default="draft", help="Lifecycle state (draft, approved, deprecated)"
)
@click.option(
    "--doc-version-status",
    default="stable",
    help="Documentation version status (stable, prerelease, archived, unknown)",
)
@click.option("--owner", default=None, help="Owner/team name")
@click.option("--policy-profile", default=None, help="Policy profile name")
@click.option(
    "--exclude-url-pattern",
    multiple=True,
    metavar="PATTERN",
    help=(
        "Additional URL path segment to exclude (e.g. 'changelog'). "
        "Can be repeated. Appended to the built-in noise list. "
        "URL builds only."
    ),
)
@click.option(
    "--no-url-filter",
    is_flag=True,
    default=False,
    help="Disable all URL noise filtering. URL builds only.",
)
@click.option(
    "--max-pages",
    default=DEFAULT_MAX_PAGES,
    show_default=True,
    type=int,
    help=(
        "Page cap for crawled builds. Hitting it truncates the crawl with a "
        "warning; the pack is built from what was fetched."
    ),
)
@click.option(
    "--user-agent",
    default=None,
    type=str,
    help=(
        "Override the User-Agent header sent on every request, including "
        "robots.txt. Some docs hosts reject the default. URL builds only."
    ),
)
@click.option(
    "--no-robots",
    is_flag=True,
    default=False,
    help=(
        "Skip robots.txt checks for crawled builds. robots.txt is respected "
        "by default; use only for public docs you have reason to mirror."
    ),
)
@click.option(
    "--rate-limit",
    default=0.5,
    show_default=True,
    type=float,
    help="Seconds to sleep between page requests. URL builds only.",
)
@click.option(
    "--max-chunk-tokens",
    default=None,
    type=int,
    help="Max tokens per chunk before overflow split. [default: 800]",
)
@click.option(
    "--min-chunk-tokens",
    default=None,
    type=int,
    help="Min tokens to emit a chunk; stubs below this are merged into the next section. [default: 20]",
)
@click.option(
    "--warn-chunk-tokens",
    default=None,
    type=int,
    help=(
        "Warn when a chunk exceeds this token count after all splits. "
        "Defaults to 2× --max-chunk-tokens."
    ),
)
@click.option(
    "--summarizer",
    type=click.Choice(["heuristic", "llm"]),
    default="heuristic",
    show_default=True,
    help=(
        "Summary strategy. 'llm' appends a model-generated sentence to each "
        "heuristic summary using a publisher-run OpenAI-compatible endpoint "
        "(requires --summarizer-url and --summarizer-model). Build-time "
        "only: chunk content is sent to that endpoint; query time stays "
        "fully local."
    ),
)
@click.option(
    "--summarizer-url",
    default=None,
    envvar="SYND_SUMMARIZER_URL",
    help=(
        "OpenAI-compatible base URL (e.g. http://localhost:8000/v1) for "
        "--summarizer llm. Env: SYND_SUMMARIZER_URL."
    ),
)
@click.option(
    "--summarizer-model",
    default=None,
    envvar="SYND_SUMMARIZER_MODEL",
    help="Served model name for --summarizer llm. Env: SYND_SUMMARIZER_MODEL.",
)
@click.option(
    "--summarizer-api-key",
    default=None,
    envvar="SYND_SUMMARIZER_API_KEY",
    help="Bearer token for the endpoint, if any. Env: SYND_SUMMARIZER_API_KEY.",
)
@click.option(
    "--summary-lockfile",
    default=None,
    type=click.Path(path_type=Path),
    help=(
        "Summary cache keyed by chunk content hash (default: "
        "<output>/<package>@<version>.summaries.jsonl). Warm rebuilds reuse "
        "it byte-for-byte — keep it next to the source for reproducible "
        "packs; delete it to regenerate all summaries."
    ),
)
def build(
    package_spec: str,
    source: str,
    output: Path,
    lifecycle: str,
    doc_version_status: str,
    owner: str | None,
    policy_profile: str | None,
    exclude_url_pattern: tuple[str, ...],
    no_url_filter: bool,
    max_pages: int,
    user_agent: str | None,
    no_robots: bool,
    rate_limit: float,
    max_chunk_tokens: int | None,
    min_chunk_tokens: int | None,
    warn_chunk_tokens: int | None,
    summarizer: str,
    summarizer_url: str | None,
    summarizer_model: str | None,
    summarizer_api_key: str | None,
    summary_lockfile: Path | None,
) -> None:
    """Build a documentation pack from source files or a URL.

    PACKAGE_SPEC is in the format package@version (e.g. my-lib@1.0.0).

    --source accepts a local directory, a URL ending in llms-full.txt or
    llms.txt (e.g. https://docs.example.com/llms-full.txt), or any other
    docs-site root URL — which is crawled: pages are discovered via
    sitemap.xml when available, otherwise by following links, confined to
    the root's host and path.

    URL builds filter out noise pages (changelogs, release notes, and for
    crawls also generated Sphinx pages like genindex/search/_modules) by
    default. Use --exclude-url-pattern to add extra patterns or
    --no-url-filter to disable filtering entirely.
    """
    try:
        pkg, ver = _parse_package_spec(package_spec)
    except SyndError as exc:
        # Malformed package@version is a usage error, not a build failure.
        console.print(f"[red]error: {exc}[/red]")
        sys.exit(EXIT_USAGE)

    output_dir = Path(output)

    resolved_max = (
        max_chunk_tokens if max_chunk_tokens is not None else _DEFAULT_MAX_CHUNK_TOKENS
    )
    resolved_min = (
        min_chunk_tokens if min_chunk_tokens is not None else _DEFAULT_MIN_CHUNK_TOKENS
    )

    summarizer_config: LlmSummarizerConfig | None = None
    if summarizer == "llm":
        if not summarizer_url or not summarizer_model:
            console.print(
                "[red]error: --summarizer llm requires --summarizer-url and "
                "--summarizer-model (or SYND_SUMMARIZER_URL / "
                "SYND_SUMMARIZER_MODEL)[/red]"
            )
            sys.exit(EXIT_USAGE)
        lockfile_path = (
            summary_lockfile
            if summary_lockfile is not None
            else Path(output) / f"{pkg}@{ver}.summaries.jsonl"
        )
        summarizer_config = LlmSummarizerConfig(
            base_url=summarizer_url,
            model=summarizer_model,
            api_key=summarizer_api_key or "",
            lockfile_path=lockfile_path,
        )

    try:
        if source.startswith(("http://", "https://")):
            output_dir.mkdir(parents=True, exist_ok=True)
            ctx_path, oversized = build_pack_from_url(
                package=pkg,
                version=ver,
                source_url=source,
                output=output_dir,
                lifecycle=lifecycle,
                doc_version_status=doc_version_status,
                owner=owner,
                policy_profile=policy_profile,
                rate_limit_sleep=rate_limit,
                excluded_url_patterns=() if no_url_filter else None,
                extra_url_patterns=tuple(exclude_url_pattern),
                max_chunk_tokens=resolved_max,
                min_chunk_tokens=resolved_min,
                warn_chunk_tokens=warn_chunk_tokens,
                max_pages=max_pages,
                user_agent=user_agent,
                respect_robots=not no_robots,
                summarizer=summarizer,
                summarizer_config=summarizer_config,
            )
        else:
            source_path = Path(source)
            if not source_path.is_dir():
                console.print(
                    f"[red]error: source directory does not exist: {source_path}[/red]"
                )
                sys.exit(EXIT_USAGE)
            output_dir.mkdir(parents=True, exist_ok=True)
            ctx_path, oversized = build_pack(
                package=pkg,
                version=ver,
                source=source_path,
                output=output_dir,
                lifecycle=lifecycle,
                doc_version_status=doc_version_status,
                owner=owner,
                policy_profile=policy_profile,
                max_chunk_tokens=resolved_max,
                min_chunk_tokens=resolved_min,
                warn_chunk_tokens=warn_chunk_tokens,
                summarizer=summarizer,
                summarizer_config=summarizer_config,
            )
        console.print(f"[green]Pack built: {ctx_path}[/green]")
        _print_crawl_summary(ctx_path)
        _print_oversized_warnings(oversized, resolved_max, warn_chunk_tokens)
    except SyndError as exc:
        console.print(f"[red]error: {exc}[/red]")
        sys.exit(exit_code_for(exc))


def _print_crawl_summary(ctx_path: Path) -> None:
    """Report crawl provenance for crawled builds; no-op for other sources.

    Reads the crawl_* fields back from the built pack's manifest — the pack
    is the artifact of record for what the crawl actually fetched.
    """
    manifest = load_manifest(ctx_path)
    if "crawl_pages_fetched" not in manifest:
        return
    console.print(
        f"  crawled {manifest['crawl_pages_fetched']} page(s) "
        f"(--max-pages {manifest['crawl_max_pages']})"
    )
    if manifest.get("crawl_truncated"):
        console.print(
            f"[yellow]  crawl truncated at {manifest['crawl_max_pages']} pages — "
            "the pack is incomplete; raise --max-pages or point --source at a "
            "deeper docs path.[/yellow]"
        )


def _print_oversized_warnings(
    oversized: list[RawChunk],
    max_chunk_tokens: int,
    warn_chunk_tokens: int | None,
) -> None:
    if not oversized:
        return
    effective_warn = (
        warn_chunk_tokens if warn_chunk_tokens is not None else 2 * max_chunk_tokens
    )
    _MAX_LISTED = 5
    console.print(
        f"[yellow]  {len(oversized)} chunk(s) exceed {effective_warn:,} tokens "
        f"— run `synd inspect` on the pack for details.[/yellow]"
    )
    for rc in oversized[:_MAX_LISTED]:
        console.print(f"[yellow]    • {rc.heading_path} ({rc.token_count:,}t)[/yellow]")
    if len(oversized) > _MAX_LISTED:
        console.print(f"[yellow]    … and {len(oversized) - _MAX_LISTED} more[/yellow]")
