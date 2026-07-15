"""Generate LLM summaries for every chunk of an eval corpus (D30 candidate).

Prototype for the `--summarizer llm` build option reserved by D3's revisit
clause: rewrite each chunk's summary with a local model so queries match
vocabulary-normalized prose instead of the doc author's wording (the
mechanism behind Context7's enrichment stage — see D30's candidate-step
addendum). Runs against the same local vLLM endpoint as generate_stage_b.py;
nothing here touches query time.

Determinism: temperature 0 (greedy) so repeated runs produce identical
summaries — the property pack_digest reproducibility (D5) will require if
this graduates to `synd build`.

Output JSONL (one line per chunk, appended; resumable by content_hash):
    {"chunk_id": 707, "content_hash": "sha256:...", "summary": "..."}

Usage:
    export SYND_GEN_VLLM_URL=http://192.168.0.214:8000/v1
    export SYND_GEN_VLLM_MODEL=<served-model-name>
    python tests/evals/generation/enrich_summaries.py \\
        --db tests/evals/generation/work/html.db \\
        --output tests/evals/generation/work/html_llm_summaries.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_MAX_TOKENS = 90
_CONTENT_CHARS = 3000  # prompt budget; MiniLM/BM25 both favor the lead anyway
_CONCURRENCY = 16  # vLLM continuous batching absorbs this comfortably

# Prompt versions (see docs/enrichment-todo.md, provenance/prompt-versioning
# item). v1 is the measured baseline; v4 adds two targeted grounding rules
# and leaves substantive chunks essentially at v1.
_PROMPTS = {
    "v1": """\
Write ONE sentence (max 30 words) describing what a developer can do or learn \
from this documentation excerpt. Use plain developer vocabulary AND the \
library's own terms for the key concepts, so both phrasings are present. \
No preamble, no markdown, just the sentence.

Section: {heading_path}

Excerpt:
{content}
""",
    "v3": """\
Write ONE sentence (max 30 words) stating the specific capability, API \
behavior, or fact this documentation excerpt documents. Use plain developer \
vocabulary AND the library's own terms for the key concepts, so both \
phrasings are present. Do not attribute an action to a tool or class unless \
the excerpt itself does. If the excerpt is an index of links, a navigation \
list, or bare attribute stubs, describe it as exactly that and name what it \
lists. No preamble, no markdown, just the sentence.

Section: {heading_path}

Excerpt:
{content}
""",
    "v4": """\
Write ONE sentence (max 30 words) describing what a developer can do or learn
from this documentation excerpt. Use plain developer vocabulary AND the
library's own terms for the key concepts, so both phrasings are present. Do not attribute an action to a tool or class unless
the excerpt itself does. If the excerpt is an index of links, a navigation
list, or bare attribute stubs, describe it as exactly that and name what it
lists. No preamble, no markdown, just the sentence.

Section: {heading_path}

Excerpt:
{content}
""",
}


def _call_vllm(prompt_text: str, base_url: str, model: str, api_key: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt_text}],
    }
    data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError(
                    f"empty content (finish_reason="
                    f"{body['choices'][0].get('finish_reason')!r})"
                )
            return " ".join(content.strip().split())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"vLLM API error {exc.code}: {exc.read().decode('utf-8', 'replace')}"
        ) from exc


def _load_done(output_path: Path) -> set[str]:
    done: set[str] = set()
    if not output_path.exists():
        return done
    with output_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["content_hash"])
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Corpus DB (chunks)")
    parser.add_argument("--output", type=Path, required=True, help="Summaries JSONL")
    parser.add_argument(
        "--prompt-version",
        choices=sorted(_PROMPTS),
        default="v1",
        help="Prompt version (v1 = measured baseline). Use a separate "
        "--output per version — the resume-by-content_hash logic cannot "
        "tell versions apart within one file.",
    )
    args = parser.parse_args()
    prompt_template = _PROMPTS[args.prompt_version]

    base_url = os.environ.get("SYND_GEN_VLLM_URL", "")
    model = os.environ.get("SYND_GEN_VLLM_MODEL", "")
    api_key = os.environ.get("SYND_GEN_VLLM_API_KEY", "")
    if not base_url or not model:
        raise SystemExit("SYND_GEN_VLLM_URL and SYND_GEN_VLLM_MODEL must be set")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, heading_path, content, content_hash FROM chunks ORDER BY id"
    ).fetchall()
    conn.close()

    done = _load_done(args.output)
    todo = [r for r in rows if r["content_hash"] not in done]
    print(f"{len(rows)} chunks, {len(done)} done, {len(todo)} to generate")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = 0
    failures = 0

    def _one(row: sqlite3.Row) -> dict[str, object]:
        prompt = prompt_template.format(
            heading_path=row["heading_path"] or "",
            content=row["content"][:_CONTENT_CHARS],
        )
        summary = _call_vllm(prompt, base_url, model, api_key)
        return {
            "chunk_id": row["id"],
            "content_hash": row["content_hash"],
            "summary": summary,
        }

    with args.output.open("a", encoding="utf-8") as out:
        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
            futures = {pool.submit(_one, row): row for row in todo}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 — log and continue
                    failures += 1
                    print(f"  FAIL chunk {row['id']}: {exc}", file=sys.stderr)
                    continue
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                completed += 1
                if completed % 200 == 0:
                    rate = completed / (time.monotonic() - started)
                    remaining = (len(todo) - completed) / rate if rate else 0
                    print(
                        f"  {completed}/{len(todo)} "
                        f"({rate:.1f}/s, ~{remaining / 60:.0f}m left)"
                    )

    print(f"done: {completed} generated, {failures} failed")
    if failures:
        print("re-run to retry failures (resumable by content_hash)")
        sys.exit(1)


if __name__ == "__main__":
    main()
