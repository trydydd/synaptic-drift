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

Usage:
    export SYND_EVAL_BASE_URL=http://<host>:8000/v1
    export SYND_EVAL_MODEL=<served-model-name>
    # export SYND_EVAL_API_KEY=...   # only if the endpoint requires auth
    python scripts/run_endtask_repeats.py [--runs 10] [--max-turns 8]

Writes:
    tests/evals/results/endtask_repeats/run_XX.json   (one per repeat)
    tests/evals/results/endtask_repeats_summary.json  (all runs + aggregate)
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS_DIR = REPO_ROOT / "tests" / "evals" / "results"
REPEATS_DIR = RESULTS_DIR / "endtask_repeats"
TASKS_PATH = REPO_ROOT / "tests" / "evals" / "datasets" / "tasks" / "seed_tasks.json"

_ARMS = ("no_docs", "with_docs")


def _build_client() -> object:
    from tests.evals.model_client import ModelClientError, client_from_env

    try:
        client = client_from_env()
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
    from tests.evals.model_client import (
        DEFAULT_MAX_TOKENS,
        DEFAULT_TEMPERATURE,
        ModelClientError,
        fetch_model_info,
    )

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
        "sampling_params": {
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "disable_thinking": client.disable_thinking,  # type: ignore[attr-defined]
        },
        "request_timeout_s": client.timeout,  # type: ignore[attr-defined]
    }


def _print_header(header: dict[str, object]) -> None:
    sp = header["sampling_params"]
    print("=== run header ===", file=sys.stderr)
    print(f"endpoint:        {header['base_url']}", file=sys.stderr)
    print(
        f"model:           {header['model']} "
        f"(root={header['model_root']}, max_model_len={header['max_model_len']}, "
        f"owned_by={header['owned_by']})",
        file=sys.stderr,
    )
    print(
        f"sampling:        temperature={sp['temperature']} max_tokens={sp['max_tokens']} "
        f"disable_thinking={sp['disable_thinking']}",
        file=sys.stderr,
    )
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
    args = parser.parse_args()

    from tests.evals.endtask import run_endtask_eval
    from tests.evals.tasks import load_tasks

    client = _build_client()
    header = _build_header(client)
    _print_header(header)
    taskset = load_tasks(TASKS_PATH)

    REPEATS_DIR.mkdir(parents=True, exist_ok=True)

    runs: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = _build_db(Path(tmp))
        try:
            for i in range(1, args.runs + 1):
                print(f"\n=== run {i}/{args.runs} ===", file=sys.stderr)
                payload = run_endtask_eval(
                    client, db, taskset, max_turns=args.max_turns
                )
                run_path = REPEATS_DIR / f"run_{i:02d}.json"
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
    summary_path = RESULTS_DIR / "endtask_repeats_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print("\n=== summary across", args.runs, "runs ===")
    for arm in _ARMS:
        a = aggregate[arm]
        print(
            f"{arm:10s} pass_rate = {a['pass_rate_mean']:.3f} +/- {a['pass_rate_stdev']:.3f}   "
            f"avg_latency_s = {a['avg_latency_s_mean']:.2f} +/- {a['avg_latency_s_stdev']:.2f}"
        )
    print(f"\nPer-run detail: {REPEATS_DIR}/run_XX.json")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
