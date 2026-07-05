"""Repeat the live end-task A/B eval N times and report mean/stdev.

A single run of the live end-task eval (tests/evals/test_endtask.py::
test_endtask_eval_live) is one sample from a nondeterministic process (model
sampling, network jitter). This script builds the eval corpus and model
client once, then calls run_endtask_eval() N times against the same
in-memory fixtures, recording each run's per-arm pass_rate and avg_latency_s,
plus the mean and (sample) standard deviation across runs.

Always disables reasoning (chat_template_kwargs.enable_thinking=False) —
this is specifically for comparing repeat variance in the fast/no-reasoning
mode, not a general-purpose repeat runner. Reuses tests/evals/endtask.py's
run_endtask_eval() and tests/evals/conftest.py's corpus-build helper directly
(no subprocess/pytest overhead per repeat).

Sampling defaults to Alibaba's recommended Qwen3 "instruct, no thinking"
preset (see tests/evals/model_client.py's SamplingParams). Override via a
TOML config file and/or CLI flags; precedence is
built-in defaults < --sampling-config file < individual CLI flags.

Usage:
    export SYND_EVAL_BASE_URL=http://<host>:8000/v1
    export SYND_EVAL_MODEL=<served-model-name>
    # export SYND_EVAL_API_KEY=...   # only if the endpoint requires auth
    python scripts/run_endtask_repeats.py [--runs 10] [--max-turns 8] \\
        [--sampling-config sampling.toml] \\
        [--temperature 0.7] [--top-p 0.8] [--top-k 20] [--min-p 0.0] \\
        [--presence-penalty 1.5] [--repetition-penalty 1.0] [--max-tokens 2048]

--sampling-config expects a TOML file shaped like:
    [sampling]
    temperature = 0.7
    top_p = 0.80

Results are grouped per model (directory named after the served model id,
reused across invocations rather than recreated) and every file within it is
timestamped, so repeated runs against the same model never clobber each
other's output. Writes:

    tests/evals/results/endtask_repeats/<model>/run_XX_<timestamp>.json
    tests/evals/results/endtask_repeats/<model>/summary_<timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS_DIR = REPO_ROOT / "tests" / "evals" / "results"
BASE_REPEATS_DIR = RESULTS_DIR / "endtask_repeats"
TASKS_PATH = REPO_ROOT / "tests" / "evals" / "datasets" / "tasks" / "seed_tasks.json"

_ARMS = ("no_docs", "with_docs")


def _safe_dirname(name: str) -> str:
    """Filesystem-safe version of a model id/name (slashes, spaces, etc)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "unknown-model"


def _resolve_sampling(args: argparse.Namespace) -> object:
    from tests.evals.model_client import ModelClientError, resolve_sampling_params

    overrides = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "min_p": args.min_p,
        "presence_penalty": args.presence_penalty,
        "repetition_penalty": args.repetition_penalty,
        "max_tokens": args.max_tokens,
    }
    try:
        return resolve_sampling_params(
            config_path=args.sampling_config, overrides=overrides
        )
    except ModelClientError as exc:  # bad/missing config file
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _build_client(sampling: object) -> object:
    from tests.evals.model_client import ModelClientError, client_from_env

    try:
        client = client_from_env(sampling=sampling)  # type: ignore[arg-type]
    except ModelClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "\nRequired: SYND_EVAL_BASE_URL and SYND_EVAL_MODEL (SYND_EVAL_API_KEY "
            "optional). Example:\n"
            "  export SYND_EVAL_BASE_URL=http://<host>:8000/v1\n"
            "  export SYND_EVAL_MODEL=<served-model-name>\n"
            "  python scripts/run_endtask_repeats.py",
            file=sys.stderr,
        )
        sys.exit(1)
    client.disable_thinking = True  # this script always measures thinking-off
    return client


def _build_header(client: object) -> dict[str, object]:
    from dataclasses import asdict

    from tests.evals.model_client import ModelClientError, fetch_model_info

    try:
        model_info = fetch_model_info(client.base_url, client.model, client.api_key)  # type: ignore[attr-defined]
    except ModelClientError as exc:
        print(f"warning: could not fetch model info: {exc}", file=sys.stderr)
        model_info = {}

    return {
        "base_url": client.base_url,  # type: ignore[attr-defined]
        "model": client.model,  # type: ignore[attr-defined]
        "model_root": model_info.get("root"),
        "max_model_len": model_info.get("max_model_len"),
        "owned_by": model_info.get("owned_by"),
        # These are what THIS client sends on every request — the OpenAI
        # /models surface has no way to read a server's configured sampling
        # defaults, so this is not server-verified, just what we requested.
        "requested_sampling_params": {
            **asdict(client.sampling),  # type: ignore[attr-defined]
            "disable_thinking": client.disable_thinking,  # type: ignore[attr-defined]
        },
        "request_timeout_s": client.timeout,  # type: ignore[attr-defined]
    }


def _print_header(header: dict[str, object]) -> None:
    sp = header["requested_sampling_params"]
    print("=== run header ===", file=sys.stderr)
    print(f"endpoint:        {header['base_url']}", file=sys.stderr)
    print(
        f"model:           {header['model']} "
        f"(root={header['model_root']}, max_model_len={header['max_model_len']}, "
        f"owned_by={header['owned_by']})",
        file=sys.stderr,
    )
    print(
        "sampling (client-requested, not server-verified):",
        file=sys.stderr,
    )
    print(f"  {sp}", file=sys.stderr)
    print(f"request_timeout: {header['request_timeout_s']}s", file=sys.stderr)


def _build_db(tmp_dir: Path) -> object:
    from synd.builder.build import build_pack
    from synd.storage.db import Database
    from tests.evals.conftest import (
        EVAL_CORPUS_DIR,
        EVAL_PACKAGE,
        EVAL_VERSION,
        load_ctx_into_db,
    )

    ctx_path, _ = build_pack(
        package=EVAL_PACKAGE,
        version=EVAL_VERSION,
        source=EVAL_CORPUS_DIR,
        output=tmp_dir,
    )
    db = Database(tmp_dir / "eval.db")
    db.create_schema()
    load_ctx_into_db(ctx_path, db)
    return db


def _mean_stdev(values: list[float]) -> tuple[float, float]:
    mean = round(statistics.mean(values), 4)
    stdev = round(statistics.stdev(values), 4) if len(values) > 1 else 0.0
    return mean, stdev


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repeat the live end-task eval N times; report mean/stdev"
    )
    parser.add_argument("--runs", type=int, default=10, help="Number of repeats")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument(
        "--sampling-config",
        type=Path,
        default=None,
        help="TOML file with a [sampling] table of overrides",
    )
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--min-p", type=float, default=None)
    parser.add_argument("--presence-penalty", type=float, default=None)
    parser.add_argument("--repetition-penalty", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    args = parser.parse_args()

    from tests.evals.endtask import run_endtask_eval
    from tests.evals.tasks import load_tasks

    sampling = _resolve_sampling(args)
    client = _build_client(sampling)
    header = _build_header(client)
    _print_header(header)
    taskset = load_tasks(TASKS_PATH)

    repeats_dir = BASE_REPEATS_DIR / _safe_dirname(client.model)  # type: ignore[attr-defined]
    repeats_dir.mkdir(parents=True, exist_ok=True)  # reused if already present
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = _build_db(Path(tmp))
        try:
            for i in range(1, args.runs + 1):
                print(f"\n=== run {i}/{args.runs} ===", file=sys.stderr)
                payload = run_endtask_eval(
                    client, db, taskset, max_turns=args.max_turns
                )
                run_path = repeats_dir / f"run_{i:02d}_{run_timestamp}.json"
                run_path.write_text(json.dumps(payload, indent=2) + "\n")
                runs.append(payload)
                for arm in _ARMS:
                    a = payload["arms"][arm]
                    print(
                        f"  {arm:10s} pass_rate={a['pass_rate']:.2f} "
                        f"avg_latency_s={a['avg_latency_s']:.2f}",
                        file=sys.stderr,
                    )
        finally:
            db.close()

    aggregate: dict[str, object] = {}
    for arm in _ARMS:
        pass_rates = [r["arms"][arm]["pass_rate"] for r in runs]
        latencies = [r["arms"][arm]["avg_latency_s"] for r in runs]
        pr_mean, pr_stdev = _mean_stdev(pass_rates)
        lat_mean, lat_stdev = _mean_stdev(latencies)
        aggregate[arm] = {
            "pass_rate_mean": pr_mean,
            "pass_rate_stdev": pr_stdev,
            "avg_latency_s_mean": lat_mean,
            "avg_latency_s_stdev": lat_stdev,
            "per_run_pass_rate": pass_rates,
            "per_run_avg_latency_s": latencies,
        }

    summary = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runs": args.runs,
            "max_turns": args.max_turns,
            **header,
        },
        "aggregate": aggregate,
    }
    summary_path = repeats_dir / f"summary_{run_timestamp}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print("\n=== summary across", args.runs, "runs ===")
    for arm in _ARMS:
        a = aggregate[arm]
        print(
            f"{arm:10s} pass_rate = {a['pass_rate_mean']:.3f} +/- {a['pass_rate_stdev']:.3f}   "
            f"avg_latency_s = {a['avg_latency_s_mean']:.2f} +/- {a['avg_latency_s_stdev']:.2f}"
        )
    print(f"\nPer-run detail: {repeats_dir}/run_XX_{run_timestamp}.json")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
