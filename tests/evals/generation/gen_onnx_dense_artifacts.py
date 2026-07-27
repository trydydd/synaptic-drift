"""Generate PyTorch-free ONNX dense artifacts (fastembed) for the S15 follow-up.

BGE-M3's only measured win was dense-encoder quality — not its sparse leg and
not BGE-M3 specifically. This script embeds the same enriched-append chunks and
gold queries with fastembed's larger ONNX dense models (bge-base/bge-large,
no torch), so l1_onnx_dense_matrix.py can test whether a PyTorch-free encoder
captures BGE-M3's dense lift without pulling torch.

Artifacts match the harness .npz conventions exactly (ids + L2-normalized vecs,
query ids keyed "<id>::<form>"), so they slot into the same scorer.

Usage (project venv — fastembed only, no torch):
    python tests/evals/generation/gen_onnx_dense_artifacts.py html \\
        --model BAAI/bge-base-en-v1.5 --tag bgebase
    python tests/evals/generation/gen_onnx_dense_artifacts.py html \\
        --model BAAI/bge-large-en-v1.5 --tag bgelarge
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

_HERE = Path(__file__).parent
_WORK = _HERE / "work"
_DATASETS = {
    "html": _HERE.parent / "datasets" / "real" / "html_v1.json",
    "pilot": _HERE.parent / "datasets" / "real" / "pilot_v1.json",
}
_QUERY_FORMS = ("query", "keyword_query")


def _chunk_texts(corpus: str) -> tuple[np.ndarray, list[str]]:
    conn = sqlite3.connect(_WORK / f"{corpus}_enriched_append.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, heading_path, summary, content FROM chunks ORDER BY id"
    ).fetchall()
    conn.close()
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    texts = [
        "\n".join(filter(None, (r["heading_path"], r["summary"], r["content"])))
        for r in rows
    ]
    return ids, texts


def _query_texts(corpus: str) -> tuple[list[str], list[str]]:
    dataset = json.loads(_DATASETS[corpus].read_text(encoding="utf-8"))
    keys: list[str] = []
    texts: list[str] = []
    for question in dataset.get("questions", []):
        for form in _QUERY_FORMS:
            text = question.get(form, "")
            if text.strip():
                keys.append(f"{question['id']}::{form}")
                texts.append(text)
    return keys, texts


def _embed(model: TextEmbedding, texts: list[str]) -> np.ndarray:
    vecs = np.array(list(model.embed(texts)), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    return vecs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", choices=("html", "pilot"))
    parser.add_argument("--model", required=True, help="fastembed model id")
    parser.add_argument("--tag", required=True, help="artifact tag, e.g. bgebase")
    args = parser.parse_args()
    corpus, tag = args.corpus, args.tag

    model = TextEmbedding(args.model)

    chunk_ids, chunk_texts = _chunk_texts(corpus)
    print(f"{corpus}/{tag}: embedding {len(chunk_ids)} chunks ({args.model})…")
    chunk_vecs = _embed(model, chunk_texts)
    np.savez(
        _WORK / f"{corpus}_enriched_append_{tag}_dense_chunk.npz",
        ids=chunk_ids,
        vecs=chunk_vecs,
    )

    q_keys, q_texts = _query_texts(corpus)
    list(model.embed(q_texts[:16]))  # warmup for a fair latency read
    t0 = time.perf_counter()
    q_vecs = _embed(model, q_texts)
    per_query_ms = 1000.0 * (time.perf_counter() - t0) / max(len(q_keys), 1)
    np.savez(
        _WORK / f"{corpus}_{tag}_dense_query.npz",
        ids=np.array(q_keys),
        vecs=q_vecs,
    )
    (_WORK / f"{corpus}_{tag}_latency.json").write_text(
        json.dumps(
            {
                "corpus": corpus,
                "model": args.model,
                "per_query_ms": round(per_query_ms, 2),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {tag} artifacts ({per_query_ms:.2f} ms/query)")


if __name__ == "__main__":
    main()
