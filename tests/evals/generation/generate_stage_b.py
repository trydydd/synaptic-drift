"""Stage B: Synthesize persona queries from capability statements.

Calls two models (Sonnet via Anthropic API, 35B-A3B via vLLM) for each
capability statement produced by Stage A. Each model generates two persona
variants covering different difficulty tiers.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export SYND_GEN_VLLM_URL=http://192.168.0.214:8000/v1
    export SYND_GEN_VLLM_MODEL=<served-model-name>   # from /v1/models
    # Optional: export SYND_GEN_VLLM_API_KEY=<token>  # if vLLM requires auth

    python tests/evals/generation/generate_stage_b.py \\
        tests/evals/generation/work/capabilities_mcp.jsonl \\
        --output tests/evals/generation/work/raw_queries_mcp.jsonl \\
        [--resume] [--only-sonnet] [--only-35b]

Output JSONL (one line per (chunk, model, persona)):
    {
        "pack_name": "mcp",
        "chunk_id": 17,
        "heading_path": "...",
        "source_url": "...",
        "content_hash": "sha256:...",
        "capability": "...",
        "model": "sonnet" | "35b",
        "persona": "expert" | "paraphrase" | "vocab_mismatch" | "hurried",
        "nl_query": "how do I mark a tool as retry-safe",
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


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_SONNET_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 200

_PROMPT_SONNET = Path(__file__).parent / "prompts" / "stage_b_sonnet.txt"
_PROMPT_35B = Path(__file__).parent / "prompts" / "stage_b_35b.txt"

_PERSONA_NAMES_SONNET = ("expert", "paraphrase")
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


def _call_anthropic(prompt_text: str, api_key: str) -> str:
    payload = {
        "model": _SONNET_MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt_text}],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _ANTHROPIC_URL,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["content"][0]["text"].strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API error {exc.code}: {body}") from exc


def _call_vllm(prompt_text: str, base_url: str, model: str, api_key: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
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
            return body["choices"][0]["message"]["content"].strip()
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
    resume: bool,
    only_sonnet: bool,
    only_35b: bool,
    delay_s: float,
) -> None:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    vllm_url = os.environ.get("SYND_GEN_VLLM_URL", "")
    vllm_model = os.environ.get("SYND_GEN_VLLM_MODEL", "")
    vllm_key = os.environ.get("SYND_GEN_VLLM_API_KEY", "")

    run_sonnet = not only_35b
    run_35b = not only_sonnet

    if run_sonnet and not anthropic_key:
        raise SystemExit("ANTHROPIC_API_KEY not set (required for Sonnet)")
    if run_35b and (not vllm_url or not vllm_model):
        raise SystemExit("SYND_GEN_VLLM_URL and SYND_GEN_VLLM_MODEL not set (required for 35B)")

    prompt_sonnet = _PROMPT_SONNET.read_text(encoding="utf-8")
    prompt_35b = _PROMPT_35B.read_text(encoding="utf-8")

    done_keys = _load_done_keys(output_path) if resume else set()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if resume else "w"

    with (
        caps_path.open(encoding="utf-8") as inp,
        output_path.open(mode, encoding="utf-8") as out,
    ):
        for line_no, line in enumerate(inp, 1):
            line = line.strip()
            if not line:
                continue
            cap = json.loads(line)
            print(f"\nB [{line_no}] chunk {cap['chunk_id']} {cap['heading_path'][:55]}")
            print(f"  capability: {cap['capability'][:80]}")

            if run_sonnet:
                key = (cap["pack_name"], cap["chunk_id"], "sonnet")
                if key in done_keys:
                    print("  [sonnet] skip (already done)")
                else:
                    prompt = prompt_sonnet.format(capability_statement=cap["capability"])
                    raw = _call_anthropic(prompt, anthropic_key)
                    results = _parse_two_persona_lines(raw, _PERSONA_NAMES_SONNET)
                    _write_records(out, cap, "sonnet", results)
                    out.flush()
                    for persona, nl, kw in results:
                        print(f"  [sonnet/{persona}] {nl[:60]} | {kw}")
                    if delay_s > 0:
                        time.sleep(delay_s)

            if run_35b:
                key = (cap["pack_name"], cap["chunk_id"], "35b")
                if key in done_keys:
                    print("  [35b] skip (already done)")
                else:
                    prompt = prompt_35b.format(capability_statement=cap["capability"])
                    raw = _call_vllm(prompt, vllm_url, vllm_model, vllm_key)
                    results = _parse_two_persona_lines(raw, _PERSONA_NAMES_35B)
                    _write_records(out, cap, "35b", results)
                    out.flush()
                    for persona, nl, kw in results:
                        print(f"  [35b/{persona}] {nl[:60]} | {kw}")
                    if delay_s > 0:
                        time.sleep(delay_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage B: persona query synthesis")
    parser.add_argument("caps_path", type=Path, help="Stage A output JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Output JSONL")
    parser.add_argument("--resume", action="store_true", help="Skip already-processed chunks")
    parser.add_argument("--only-sonnet", action="store_true", help="Run Sonnet only")
    parser.add_argument("--only-35b", action="store_true", help="Run 35B only")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls")
    args = parser.parse_args()
    generate_stage_b(
        args.caps_path,
        args.output,
        args.resume,
        args.only_sonnet,
        args.only_35b,
        args.delay,
    )
    print("\nStage B complete.")


if __name__ == "__main__":
    main()
