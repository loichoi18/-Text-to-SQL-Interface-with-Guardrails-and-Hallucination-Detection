"""Multi-query agreement check.

We ask the LLM for an alternative SQL formulation (different join
order, CTE vs subquery, aggregating over a different table). Both
queries run; we compare the result sets. Agreement is a strong
correctness signal, disagreement is the strongest hallucination
flag we have.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.sql.executor import ExecutionResult, run_safely


@dataclass
class MultiQueryReport:
    primary_rows: int
    alternative_rows: int
    agreement: float  # 0.0 - 1.0
    alternative_sql: str | None
    error: str | None = None


def compare_results(primary: ExecutionResult, alternative: ExecutionResult) -> float:
    """Compute an agreement score in [0, 1] between two result sets.

    * Empty vs empty       -> 1.0
    * Same single value    -> 1.0 (handles sum/count/aggregate cases)
    * Same row sets        -> 1.0 (order-insensitive)
    * Otherwise            -> Jaccard of row tuples (column-insensitive)
    """
    if primary.row_count == 0 and alternative.row_count == 0:
        return 1.0

    # Single scalar comparison.
    if primary.row_count == 1 == alternative.row_count and len(primary.rows[0]) == 1 == len(alternative.rows[0]):
        a = primary.rows[0][0]
        b = alternative.rows[0][0]
        return 1.0 if _scalar_equal(a, b) else 0.0

    set_a = {_canonical(r) for r in primary.rows}
    set_b = {_canonical(r) for r in alternative.rows}
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _canonical(row) -> tuple:
    return tuple(_normalize(v) for v in row)


def _normalize(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


def _scalar_equal(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-4
        except (TypeError, ValueError):
            return False
    return a == b


def run_multi_query(primary: ExecutionResult, alternative_sql: str | None) -> MultiQueryReport:
    if not alternative_sql:
        return MultiQueryReport(
            primary_rows=primary.row_count,
            alternative_rows=0,
            agreement=0.0,
            alternative_sql=None,
            error="no alternative produced",
        )
    try:
        alt = run_safely(alternative_sql, capture_explain=False)
    except Exception as exc:
        return MultiQueryReport(
            primary_rows=primary.row_count,
            alternative_rows=0,
            agreement=0.0,
            alternative_sql=alternative_sql,
            error=str(exc),
        )
    agreement = compare_results(primary, alt)
    return MultiQueryReport(
        primary_rows=primary.row_count,
        alternative_rows=alt.row_count,
        agreement=round(agreement, 4),
        alternative_sql=alt.sql,
    )
