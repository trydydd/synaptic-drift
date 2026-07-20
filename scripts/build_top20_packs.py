"""Build .ctx packs for the top-20 most intentionally installed Python packages.

Distribution-pack builder / crawler acceptance harness (see
docs/top20-python-packages.md): 19 of the 20 packages are built — pydantic
from its llms.txt, everything else crawled from its docs root. boto3 is
deliberately excluded from the default run (docs.aws.amazon.com is a
~10k-page Sphinx site); its recipe is printed at the end instead.

Run (network, slow — sequential polite crawling):
    python scripts/build_top20_packs.py [--only PKG ...] [--output DIR]

Enriched (0.4.0 D31) packs — appends an LLM-generated sentence to each
summary via a publisher-run OpenAI-compatible endpoint; query time stays
fully local:
    python scripts/build_top20_packs.py \\
        --summarizer llm \\
        --summarizer-url http://localhost:8000/v1 \\
        --summarizer-model <served-model>
The summarizer choice is applied uniformly to every target; each pack gets
its own default summary lockfile (<output>/<pkg>@<ver>.summaries.jsonl) so
warm rebuilds reuse cached summaries byte-for-byte.

Already-built packs are skipped, so re-running after a partial build is
safe. Each built pack is verified with `synd verify`, and a coverage report
is printed for pasting into docs/top20-python-packages.md.

Versions below are pinned for reproducible pack names; check PyPI and bump
before building packs intended for publication.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

OUTDIR = Path("packs")

_MATPLOTLIB_UA = (
    "Mozilla/5.0 (compatible; synd/0.4; +https://github.com/trydydd/synaptic-drift)"
)

# fastapi.tiangolo.com serves full translated copies of the docs under
# two-letter (plus em/zh-hant) path prefixes. Without excluding them the
# 500-page budget fills up with duplicate pages in other languages.
_FASTAPI_LANGS = (
    "az bn de em es fa fr he hi hu id it ja ko nl pl pt ru tr uk ur vi yo zh zh-hant"
).split()
_FASTAPI_EXCLUDES = [
    flag for lang in _FASTAPI_LANGS for flag in ("--exclude-url-pattern", lang)
]


@dataclass
class Target:
    package: str
    version: str
    source_url: str
    extra_flags: list[str] = field(default_factory=list)
    notes: str = ""


# Docs roots from docs/top20-python-packages.md. boto3 (rank 4) is excluded —
# see the recipe printed at the end of the run.
TARGETS: list[Target] = [
    Target("requests", "2.34.2", "https://requests.readthedocs.io/en/latest/"),
    Target("numpy", "2.5.1", "https://numpy.org/doc/stable/"),
    Target("pandas", "3.0.3", "https://pandas.pydata.org/docs/"),
    # pydantic is the one top-20 package with llms.txt (curated index build).
    Target(
        "pydantic",
        "2.13.4",
        "https://docs.pydantic.dev/latest/llms.txt",
        notes="llms.txt build, not crawled",
    ),
    Target("click", "8.4.2", "https://click.palletsprojects.com/en/stable/"),
    Target("pytest", "9.1.1", "https://docs.pytest.org/en/stable/"),
    Target("sqlalchemy", "2.0.51", "https://docs.sqlalchemy.org/en/20/"),
    Target(
        "fastapi",
        "0.139.2",
        "https://fastapi.tiangolo.com/",
        extra_flags=_FASTAPI_EXCLUDES,
        notes="translations excluded so the page budget goes to English docs",
    ),
    Target("flask", "3.1.3", "https://flask.palletsprojects.com/en/stable/"),
    Target(
        "django",
        "6.0.7",
        "https://docs.djangoproject.com/en/6.0/",
        notes="large site; expect truncation at the default --max-pages",
    ),
    Target("pillow", "12.3.0", "https://pillow.readthedocs.io/en/stable/"),
    Target("scipy", "1.18.0", "https://docs.scipy.org/doc/scipy/"),
    Target(
        "matplotlib",
        "3.11.0",
        "https://matplotlib.org/stable/",
        extra_flags=["--user-agent", _MATPLOTLIB_UA],
        notes="403s the default User-Agent",
    ),
    Target("httpx", "0.28.1", "https://www.python-httpx.org/"),
    Target("celery", "5.6.3", "https://docs.celeryq.dev/en/stable/"),
    Target("redis", "8.0.1", "https://redis-py.readthedocs.io/en/stable/"),
    Target("pyyaml", "6.0.3", "https://pyyaml.org/"),
    Target(
        "python-dotenv",
        "1.2.2",
        "https://saurabh-kumar.com/python-dotenv/",
        notes="tiny single-project site",
    ),
    Target("rich", "15.0.0", "https://rich.readthedocs.io/en/stable/"),
]

_BOTO3_RECIPE = """\
boto3 (excluded from this run — docs.aws.amazon.com hosts ~10k+ pages):
    synd build boto3@<version> \\
        --source https://boto3.amazonaws.com/v1/documentation/api/latest/guide/ \\
        --max-pages 1000 --output packs
Scope --source at the developer-guide subtree (not the API reference root)
or the crawl will truncate far below useful coverage."""


def _find_synd() -> str:
    for candidate in (".venv/bin/synd", "synd"):
        try:
            subprocess.run([candidate, "--help"], capture_output=True)
            return candidate
        except FileNotFoundError:
            pass
    print("error: synd CLI not found. Run: pip install -e '.[all]'", file=sys.stderr)
    sys.exit(1)


def _summarizer_flags(args: argparse.Namespace) -> list[str]:
    """Translate the summarizer args into `synd build` flags for every target.

    For --summarizer llm, --summarizer-url and --summarizer-model must be
    resolvable (from the flag or the matching SYND_SUMMARIZER_* env var);
    validated up front so a long crawl does not start only to fail on the
    first target's endpoint call. Each pack uses synd's default per-pack
    summary lockfile, so no lockfile flag is threaded here.
    """
    if args.summarizer != "llm":
        return []
    url = args.summarizer_url or os.environ.get("SYND_SUMMARIZER_URL")
    model = args.summarizer_model or os.environ.get("SYND_SUMMARIZER_MODEL")
    if not url or not model:
        print(
            "error: --summarizer llm requires --summarizer-url and "
            "--summarizer-model (or SYND_SUMMARIZER_URL / SYND_SUMMARIZER_MODEL)",
            file=sys.stderr,
        )
        sys.exit(2)
    flags = [
        "--summarizer",
        "llm",
        "--summarizer-url",
        url,
        "--summarizer-model",
        model,
    ]
    api_key = args.summarizer_api_key or os.environ.get("SYND_SUMMARIZER_API_KEY")
    if api_key:
        flags += ["--summarizer-api-key", api_key]
    return flags


def _pack_stats(ctx_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(ctx_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        chunk_count = zf.read("chunks.jsonl").count(b"\n")
    return {
        "chunks": chunk_count,
        "pages": manifest.get("pages", 0),
        "crawl_pages_fetched": manifest.get("crawl_pages_fetched", "-"),
        "truncated": manifest.get("crawl_truncated", "-"),
        # 0.4.0 D31 provenance — absent on heuristic-only packs.
        "summarizer": manifest.get("summarizer", "heuristic"),
        "summarizer_model": manifest.get("summarizer_model", "-"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="PKG",
        help="Build only the named package(s); repeatable.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTDIR,
        help=f"Directory for .ctx files (default: {OUTDIR})",
    )
    parser.add_argument(
        "--summarizer",
        choices=("heuristic", "llm"),
        default="heuristic",
        help=(
            "Summary strategy for every target (0.4.0 D31). 'llm' appends a "
            "model-generated sentence via --summarizer-url/--summarizer-model."
        ),
    )
    parser.add_argument(
        "--summarizer-url",
        default=None,
        metavar="URL",
        help="OpenAI-compatible base URL for --summarizer llm (or SYND_SUMMARIZER_URL).",
    )
    parser.add_argument(
        "--summarizer-model",
        default=None,
        metavar="MODEL",
        help="Served model name for --summarizer llm (or SYND_SUMMARIZER_MODEL).",
    )
    parser.add_argument(
        "--summarizer-api-key",
        default=None,
        metavar="KEY",
        help="Bearer token for the endpoint, if any (or SYND_SUMMARIZER_API_KEY).",
    )
    args = parser.parse_args()

    # Fail fast before crawling anything if enrichment is under-specified.
    # synd would reject each build the same way, but this keeps a long run
    # from starting only to error out on the first target's endpoint call.
    summarizer_flags = _summarizer_flags(args)

    targets = TARGETS
    if args.only:
        wanted = {p.lower() for p in args.only}
        targets = [t for t in TARGETS if t.package.lower() in wanted]
        if not targets:
            print(f"error: no targets match {sorted(wanted)}", file=sys.stderr)
            sys.exit(2)

    args.output.mkdir(parents=True, exist_ok=True)
    synd = _find_synd()
    rows: list[tuple[str, str]] = []

    for target in targets:
        spec = f"{target.package}@{target.version}"
        pack_path = args.output / f"{spec}.ctx"

        if not pack_path.exists():
            print(f"build  {spec:<28} {target.source_url}")
            build = subprocess.run(
                [
                    synd,
                    "build",
                    spec,
                    "--source",
                    target.source_url,
                    "--output",
                    str(args.output),
                    *target.extra_flags,
                    *summarizer_flags,
                ],
                capture_output=True,
                text=True,
            )
            if build.returncode != 0 or not pack_path.exists():
                detail = (build.stdout + build.stderr).strip().splitlines()
                rows.append((spec, f"BUILD FAILED: {detail[-1] if detail else '?'}"))
                continue
        else:
            print(f"skip   {spec:<28} already built")

        verify = subprocess.run(
            [synd, "verify", str(pack_path)], capture_output=True, text=True
        )
        stats = _pack_stats(pack_path)
        verdict = "verify OK" if verify.returncode == 0 else "VERIFY FAILED"
        note = f"  ({target.notes})" if target.notes else ""
        enrich = (
            f" summarizer={stats['summarizer']}({stats['summarizer_model']})"
            if stats["summarizer"] != "heuristic"
            else ""
        )
        rows.append(
            (
                spec,
                f"{verdict}  pages={stats['pages']} "
                f"crawled={stats['crawl_pages_fetched']} "
                f"truncated={stats['truncated']} chunks={stats['chunks']}"
                f"{enrich}{note}",
            )
        )

    print("\n=== top-20 coverage report ===")
    for spec, summary in rows:
        print(f"{spec:<30} {summary}")
    failed = [spec for spec, summary in rows if "FAILED" in summary]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} built and verified")
    if failed:
        print(f"failed: {', '.join(failed)}")
    print(f"\n{_BOTO3_RECIPE}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
