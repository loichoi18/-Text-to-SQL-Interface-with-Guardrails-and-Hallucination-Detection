"""Combined confidence scoring.

Five signals, each in [0, 1]:
  * syntax_validity    - did parsing & guardrails pass cleanly? (1 if yes)
  * back_translation   - semantic similarity between original Q and back-translated Q
  * sanity_score       - derived from sanity-check findings
  * multi_query        - agreement score between primary and alternative
  * schema_coverage    - did the query reference plausible tables for this question?

The composite score is a weighted average of present signals. We
return both the composite and the breakdown so the UI can show the
user *why* confidence is what it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from app.validation.multi_query import MultiQueryReport
from app.validation.sanity import SanityReport, passes_to_score

if TYPE_CHECKING:
    from app.validation.back_translation import BackTranslationResult


WEIGHTS = {
    "syntax_validity":  0.10,
    "back_translation": 0.30,
    "sanity_score":     0.20,
    "multi_query":      0.30,
    "schema_coverage":  0.10,
}


@dataclass
class ConfidenceBreakdown:
    syntax_validity:  float
    back_translation: float
    sanity_score:     float
    multi_query:      float
    schema_coverage:  float
    composite:        float
    notes:            list[str] = field(default_factory=list)


def schema_coverage_score(
    tables_used: list[str],
    candidate_tables: list[str],
) -> float:
    """How many of the schema-filter's relevant tables actually showed up
    in the SQL? Penalises queries that hit too few or unexpected tables.
    """
    if not candidate_tables:
        return 0.5
    overlap = set(t.lower() for t in tables_used) & set(t.lower() for t in candidate_tables)
    return min(1.0, len(overlap) / max(1, min(3, len(candidate_tables))))


def compute_confidence(
    *,
    syntax_ok: bool,
    back_translation: Optional["BackTranslationResult"],
    sanity: SanityReport,
    multi_query: Optional[MultiQueryReport],
    tables_used: list[str],
    candidate_tables: list[str],
) -> ConfidenceBreakdown:
    syntax_score = 1.0 if syntax_ok else 0.0
    bt_score = back_translation.similarity if back_translation else 0.0
    sanity_score = passes_to_score(sanity)
    mq_score = multi_query.agreement if multi_query else 0.0
    cov_score = schema_coverage_score(tables_used, candidate_tables)

    parts = {
        "syntax_validity":  syntax_score,
        "back_translation": bt_score,
        "sanity_score":     sanity_score,
        "multi_query":      mq_score,
        "schema_coverage":  cov_score,
    }
    composite = sum(WEIGHTS[k] * v for k, v in parts.items())

    notes: list[str] = []
    if not syntax_ok:
        notes.append("SQL failed structural validation.")
    if back_translation and not back_translation.aligned:
        notes.append(
            f"Back-translated question diverges from the original "
            f"(similarity {back_translation.similarity:.2f} < {back_translation.threshold:.2f})."
        )
    if not sanity.passed:
        notes.append("Result-level sanity checks failed.")
    if multi_query and multi_query.agreement < 0.95:
        notes.append(
            f"Alternative SQL produced different results (agreement {multi_query.agreement:.2f})."
        )

    return ConfidenceBreakdown(
        syntax_validity=round(syntax_score, 3),
        back_translation=round(bt_score, 3),
        sanity_score=round(sanity_score, 3),
        multi_query=round(mq_score, 3),
        schema_coverage=round(cov_score, 3),
        composite=round(composite, 3),
        notes=notes,
    )
