"""Stage B (weak half): synthesize persona queries via the local vLLM model.

Generates the vocab_mismatch and hurried persona queries with a local vLLM
model. The strong-model half (expert, paraphrase personas) is authored by
Claude in a Claude Code session and merged by finalize_stage_b.py — this
script no longer calls the Anthropic API.

Model label is fixed as "35b" (representing the weak-model half, regardless of
the actual served model). Pilot run used Qwen3.6:27b (fp8 quantization with
unquantized KV cache) with reasoning disabled.

Always appends to --output and skips (pack_name, chunk_id, model) keys already
present, so it is safe to re-run after an interruption and composes with
finalize_stage_b.py run in either order.

Usage:
    export SYND_GEN_VLLM_URL=http://192.168.0.214:8000/v1
    export SYND_GEN_VLLM_MODEL=<served-model-name>   # from /v1/models
    # Optional: export SYND_GEN_VLLM_API_KEY=<token>  # if vLLM requires auth

    python tests/evals/generation/generate_stage_b.py \\
        tests/evals/generation/work/capabilities_mcp.jsonl \\
        --output tests/evals/generation/work/raw_queries_mcp.jsonl

Output JSONL (one line per (chunk, persona)):
    {
        "pack_name": "mcp",
        "chunk_id": 17,
        "heading_path": "...",
        "source_url": "...",
        "content_hash": "sha256:...",
        "capability": "...",
        "model": "35b",
        "persona": "vocab_mismatch" | "hurried",
        "nl_query": "how do i mark a tool as retry-safe",
        "keyword_query": "tool retry safe idempotent"
    }
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

_MAX_TOKENS = 200
_MODEL_LABEL = "35b"

_PROMPT_35B = Path(__file__).parent / "prompts" / "stage_b_35b.txt"

_PERSONA_NAMES_35B = ("vocab_mismatch", "hurried")


def _load_done_keys(output_path: Path) -> set[tuple[str, int, str]]:
    """Return set of (pack_name, chunk_id, model) already in output."""
    done: set[tuple[str, int, str]] = set()
    if not output_path.exists():
        return done
    with output_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add((rec["pack_name"], rec["chunk_id"], rec["model"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def _call_vllm(prompt_text: str, base_url: str, model: str, api_key: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        # Qwen3's reasoning mode otherwise burns the whole max_tokens budget
        # on a <think> block, leaving `content` null (finish_reason "length").
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt_text}],
    }
    data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError(
                    f"vLLM returned empty content (finish_reason="
                    f"{body['choices'][0].get('finish_reason')!r}); raw: {body}"
                )
            return content.strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"vLLM API error {exc.code}: {body}") from exc


def _parse_two_persona_lines(
    raw: str, persona_names: tuple[str, str]
) -> list[tuple[str, str, str]]:
    """Parse 4-line Stage B output → list of (persona, nl_query, keyword_query).

    Expected format (from prompts):
        Line 1: NL query for persona 1
        Line 2: keyword query for persona 1
        Line 3: NL query for persona 2
        Line 4: keyword query for persona 2
    """
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if len(lines) < 4:
        lines += [""] * (4 - len(lines))
    results = []
    for i, persona in enumerate(persona_names):
        nl = lines[i * 2] if i * 2 < len(lines) else ""
        kw = lines[i * 2 + 1] if i * 2 + 1 < len(lines) else ""
        results.append((persona, nl, kw))
    return results


def _write_records(
    out_fh,
    cap: dict,
    model_label: str,
    persona_results: list[tuple[str, str, str]],
) -> None:
    for persona, nl, kw in persona_results:
        record = {
            "pack_name": cap["pack_name"],
            "chunk_id": cap["chunk_id"],
            "heading_path": cap["heading_path"],
            "source_url": cap["source_url"],
            "content_hash": cap["content_hash"],
            "capability": cap["capability"],
            "model": model_label,
            "persona": persona,
            "nl_query": nl,
            "keyword_query": kw,
        }
        out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def generate_stage_b(
    caps_path: Path,
    output_path: Path,
    delay_s: float,
) -> None:
    vllm_url = os.environ.get("SYND_GEN_VLLM_URL", "")
    vllm_model = os.environ.get("SYND_GEN_VLLM_MODEL", "")
    vllm_key = os.environ.get("SYND_GEN_VLLM_API_KEY", "")

    if not vllm_url or not vllm_model:
        raise SystemExit(
            "SYND_GEN_VLLM_URL and SYND_GEN_VLLM_MODEL not set (required for 35B)"
        )

    prompt_35b = _PROMPT_35B.read_text(encoding="utf-8")

    done_keys = _load_done_keys(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        caps_path.open(encoding="utf-8") as inp,
        output_path.open("a", encoding="utf-8") as out,
    ):
        for line_no, line in enumerate(inp, 1):
            line = line.strip()
            if not line:
                continue
            cap = json.loads(line)
            print(f"\nB [{line_no}] chunk {cap['chunk_id']} {cap['heading_path'][:55]}")
            print(f"  capability: {cap['capability'][:80]}")

            key = (cap["pack_name"], cap["chunk_id"], _MODEL_LABEL)
            if key in done_keys:
                print("  [35b] skip (already done)")
                continue

            prompt = prompt_35b.format(capability_statement=cap["capability"])
            raw = _call_vllm(prompt, vllm_url, vllm_model, vllm_key)
            results = _parse_two_persona_lines(raw, _PERSONA_NAMES_35B)
            _write_records(out, cap, _MODEL_LABEL, results)
            out.flush()
            for persona, nl, kw in results:
                print(f"  [35b/{persona}] {nl[:60]} | {kw}")
            if delay_s > 0:
                time.sleep(delay_s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage B weak half: persona query synthesis via vLLM"
    )
    parser.add_argument(
        "caps_path",
        type=Path,
        help="Canonical capabilities JSONL (finalize_stage_a.py output)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSONL (appended; done chunks skipped)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between API calls"
    )
    args = parser.parse_args()
    generate_stage_b(args.caps_path, args.output, args.delay)
    print("\nStage B (weak half) complete.")


if __name__ == "__main__":
    main()
