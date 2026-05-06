"""SQL-to-question back-translation check.

Hypothesis: if the model can't describe what its own SQL does in a way
that lines up with the original question, the SQL probably doesn't
answer that question. We:
1. Ask the LLM "what question does this SQL answer?".
2. Embed both the original question and the back-translation.
3. Cosine-similarity. Below a threshold => flagged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.llm.client import LLMClient
from app.llm.prompts import BACK_TRANSLATE_SYSTEM, build_back_translation_prompt
from app.schema.filter import _model  # reuse the cached embedder
from app.schema.introspector import SchemaInfo


@dataclass
class BackTranslationResult:
    original_question: str
    back_translation: str
    similarity: float
    aligned: bool
    threshold: float


def back_translate_and_score(
    client: LLMClient,
    question: str,
    sql: str,
    schema: SchemaInfo,
    table_subset: list[str] | None = None,
    *,
    threshold: float = 0.55,
) -> BackTranslationResult:
    user_prompt = build_back_translation_prompt(sql, schema, table_subset)
    bt = client.generate_text(system=BACK_TRANSLATE_SYSTEM, user=user_prompt)
    similarity = _semantic_similarity(question, bt)
    return BackTranslationResult(
        original_question=question,
        back_translation=bt,
        similarity=round(similarity, 4),
        aligned=similarity >= threshold,
        threshold=threshold,
    )


def _semantic_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    try:
        model = _model()
        vecs = model.encode([a, b], normalize_embeddings=True)
    except Exception:
        # Fallback to word-overlap if embedding model is unavailable.
        return _word_overlap(a, b)
    return float(vecs[0] @ vecs[1])


def _word_overlap(a: str, b: str) -> float:
    """Cheap fallback similarity. Token-level Jaccard."""
    sa = {t.lower() for t in a.split() if len(t) > 2}
    sb = {t.lower() for t in b.split() if len(t) > 2}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
