"""Schema relevance filtering.

For large databases the full schema can blow past the model's context
budget, and even when it fits, irrelevant tables increase the chance
of bad joins. We embed a per-table description (table name + comment +
column names + comments) and the user's question, then keep tables
whose cosine similarity exceeds a threshold (or top-K, whichever is
greater).

The embedding model is loaded lazily and cached process-wide.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

import numpy as np

from app.schema.introspector import SchemaInfo, TableInfo


@dataclass
class FilteredSchema:
    tables: list[str]
    scores: dict[str, float]
    method: str  # "all" | "embedding"


def _table_description(t: TableInfo) -> str:
    parts = [t.name]
    if t.comment:
        parts.append(t.comment)
    for c in t.columns:
        parts.append(c.name)
        if c.comment:
            parts.append(c.comment)
    return " | ".join(parts)


@lru_cache(maxsize=1)
def _model():
    # Imported lazily so unit tests that don't need embeddings don't
    # pay the model load cost.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denom if denom else 0.0


def filter_relevant_tables(
    schema: SchemaInfo,
    question: str,
    *,
    top_k: int = 6,
    threshold: float = 0.18,
    always_include: Iterable[str] = (),
    max_tables_in_full_schema: int = 8,
) -> FilteredSchema:
    """Return the subset of tables most relevant to `question`.

    For small schemas (<= max_tables_in_full_schema) we skip embedding
    altogether and return everything; the cost of context is low and
    filtering risks dropping a table the user actually needs.
    """
    all_names = [t.name for t in schema.tables]
    if len(all_names) <= max_tables_in_full_schema:
        return FilteredSchema(tables=all_names, scores={n: 1.0 for n in all_names}, method="all")

    try:
        model = _model()
    except Exception:
        # If sentence-transformers fails to load (offline, etc.), be safe
        # and return the full schema.
        return FilteredSchema(tables=all_names, scores={n: 1.0 for n in all_names}, method="all")

    table_texts = [_table_description(t) for t in schema.tables]
    embeddings  = model.encode([question] + table_texts, normalize_embeddings=True)
    q_vec       = embeddings[0]
    t_vecs      = embeddings[1:]

    scores: dict[str, float] = {
        name: float(_cosine(q_vec, t_vecs[i])) for i, name in enumerate(all_names)
    }
    forced = set(always_include)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    chosen: list[str] = []
    for name, sc in ranked:
        if sc >= threshold or len(chosen) < top_k or name in forced:
            chosen.append(name)
        if len(chosen) >= max(top_k, len(forced)):
            # We've satisfied top_k and forced; remaining tables only
            # qualify if above threshold.
            for nm, s in ranked[len(chosen):]:
                if s >= threshold or nm in forced:
                    chosen.append(nm)
            break
    # Ensure forced inclusions are present even if ranking was short.
    for f in forced:
        if f not in chosen and f in scores:
            chosen.append(f)

    return FilteredSchema(tables=chosen, scores=scores, method="embedding")
