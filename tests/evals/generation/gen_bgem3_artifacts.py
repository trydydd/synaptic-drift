"""Generate BGE-M3 dense + sparse artifacts for the S15 head-to-head.

Runs in an ISOLATED venv that carries torch + FlagEmbedding (BGE-M3 has no
PyTorch-free path — see docs/spikes.yaml S15 feasibility gate). It writes only
small .npz artifacts back into tests/evals/generation/work/, so the project
venv's scorer (tests/evals/l1_bgem3_matrix.py) never needs torch.

Everything is matched to the shipped enriched-append pipeline so BGE-M3 is
judged on identical footing with the current unicode61-BM25 + MiniLM stack:

  * indexed text  = heading_path \\n summary \\n content, ordered by chunk id,
                    read from <corpus>_enriched_append.db (the D31 form).
  * query text    = the gold query / keyword_query, keyed "<id>::<form>",
                    matching l1_rrf_matrix.py's query-embedding keys.

Dense vectors are L2-normalized (cosine == dot). Sparse lexical weights are
stored CSR-style (indptr/indices/data over BGE-M3's token-id space) so the
scorer can compute sparse dot products with numpy alone — no scipy.

Usage (in the isolated venv):
    python tests/evals/generation/gen_bgem3_artifacts.py html
    python tests/evals/generation/gen_bgem3_artifacts.py pilot
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
from FlagEmbedding import BGEM3FlagModel

_HERE = Path(__file__).parent
_WORK = _HERE / "work"
_DATASETS = {
    "html": _HERE.parent / "datasets" / "real" / "html_v1.json",
    "pilot": _HERE.parent / "datasets" / "real" / "pilot_v1.json",
}
_QUERY_FORMS = ("query", "keyword_query")
_MODEL = "BAAI/bge-m3"
_MAX_LEN = 1024  # chunk cap is 800 tokens; +summary/heading fits comfortably.
_BATCH = 16


def _chunk_texts(corpus: str) -> tuple[np.ndarray, list[str]]:
    """(ids, texts) from the enriched-append porter twin, ordered by id.

    Same recipe as build_enriched_artifacts.py so the indexed side is identical
    to what MiniLM saw."""
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
    """(keys, texts) where key = '<question id>::<form>'."""
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


def _encode(
    model: BGEM3FlagModel, texts: list[str]
) -> tuple[np.ndarray, list[dict[int, float]]]:
    out = model.encode(
        texts,
        batch_size=_BATCH,
        max_length=_MAX_LEN,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = np.asarray(out["dense_vecs"], dtype=np.float32)
    dense /= np.linalg.norm(dense, axis=1, keepdims=True) + 1e-12
    sparse = [
        {int(t): float(w) for t, w in row.items()} for row in out["lexical_weights"]
    ]
    return dense, sparse


def _save_sparse(path: Path, ids: np.ndarray, sparse: list[dict[int, float]]) -> None:
    """CSR-style: indptr[i]:indptr[i+1] slices indices/data for row i."""
    indptr = np.zeros(len(sparse) + 1, dtype=np.int64)
    indices_parts: list[np.ndarray] = []
    data_parts: list[np.ndarray] = []
    for i, row in enumerate(sparse):
        toks = sorted(row)
        indptr[i + 1] = indptr[i] + len(toks)
        indices_parts.append(np.array(toks, dtype=np.int64))
        data_parts.append(np.array([row[t] for t in toks], dtype=np.float32))
    indices = (
        np.concatenate(indices_parts) if indices_parts else np.zeros(0, dtype=np.int64)
    )
    data = np.concatenate(data_parts) if data_parts else np.zeros(0, dtype=np.float32)
    np.savez(path, ids=ids, indptr=indptr, indices=indices, data=data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", choices=("html", "pilot"))
    args = parser.parse_args()
    corpus = args.corpus

    model = BGEM3FlagModel(_MODEL, use_fp16=False)

    chunk_ids, chunk_texts = _chunk_texts(corpus)
    print(f"{corpus}: encoding {len(chunk_ids)} chunks (BGE-M3 dense+sparse)…")
    t0 = time.perf_counter()
    chunk_dense, chunk_sparse = _encode(model, chunk_texts)
    chunk_secs = time.perf_counter() - t0
    print(
        f"  chunks encoded in {chunk_secs:.1f}s ({len(chunk_ids) / chunk_secs:.1f}/s)"
    )

    q_keys, q_texts = _query_texts(corpus)
    print(f"{corpus}: encoding {len(q_keys)} queries…")
    t0 = time.perf_counter()
    q_dense, q_sparse = _encode(model, q_texts)
    q_secs = time.perf_counter() - t0
    per_query_ms = 1000.0 * q_secs / max(len(q_keys), 1)
    print(f"  queries encoded in {q_secs:.1f}s ({per_query_ms:.1f} ms/query)")

    np.savez(
        _WORK / f"{corpus}_enriched_append_bgem3_dense_chunk.npz",
        ids=chunk_ids,
        vecs=chunk_dense,
    )
    _save_sparse(
        _WORK / f"{corpus}_enriched_append_bgem3_sparse_chunk.npz",
        chunk_ids,
        chunk_sparse,
    )
    q_key_arr = np.array(q_keys)
    np.savez(_WORK / f"{corpus}_bgem3_dense_query.npz", ids=q_key_arr, vecs=q_dense)
    _save_sparse(_WORK / f"{corpus}_bgem3_sparse_query.npz", q_key_arr, q_sparse)

    latency = {
        "corpus": corpus,
        "model": _MODEL,
        "max_length": _MAX_LEN,
        "batch_size": _BATCH,
        "n_chunks": int(len(chunk_ids)),
        "chunk_encode_secs": round(chunk_secs, 2),
        "n_queries": len(q_keys),
        "query_encode_secs": round(q_secs, 2),
        "per_query_ms": round(per_query_ms, 2),
    }
    (_WORK / f"{corpus}_bgem3_latency.json").write_text(
        json.dumps(latency, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  wrote artifacts + latency for {corpus}")


if __name__ == "__main__":
    main()
