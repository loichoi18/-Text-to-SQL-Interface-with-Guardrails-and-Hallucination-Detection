"""Tests for the validation layer (sanity, multi-query, confidence)."""
from __future__ import annotations

from app.sql.executor import ExecutionResult
from app.validation.confidence import compute_confidence, schema_coverage_score
from app.validation.multi_query import compare_results
from app.validation.sanity import check_results, passes_to_score


# ---------- sanity checks ---------- #
def _result(rows, columns=("a",)):
    return ExecutionResult(
        sql="SELECT 1",
        columns=list(columns),
        rows=rows,
        row_count=len(rows),
        execution_ms=1.0,
    )


def test_sanity_passes_clean_results() -> None:
    r = _result([[1], [2], [3]])
    rep = check_results(r, question="show me ids")
    assert rep.passed
    assert rep.warnings == 0
    assert passes_to_score(rep) == 1.0


def test_sanity_flags_negative_aggregate() -> None:
    r = _result([[-100]], columns=["total_revenue"])
    rep = check_results(r, question="total revenue")
    assert any(f.code == "NEGATIVE_AGGREGATE" for f in rep.findings)


def test_sanity_flags_null_heavy_column() -> None:
    rows = [[None], [None], [None], [1]]
    r = _result(rows, columns=["customer_name"])
    rep = check_results(r, question="list customers")
    assert any(f.code == "NULL_HEAVY_COLUMN" for f in rep.findings)


def test_sanity_flags_empty_for_list_question() -> None:
    r = _result([])
    rep = check_results(r, question="list all customers")
    assert any(f.code == "EMPTY_RESULT" for f in rep.findings)


def test_sanity_does_not_flag_empty_for_aggregate_question() -> None:
    r = _result([])
    rep = check_results(r, question="how many cancelled orders for impossible filter")
    # Aggregate-style question: empty isn't a failure mode here.
    assert not any(f.code == "EMPTY_RESULT" for f in rep.findings)


# ---------- multi-query agreement ---------- #
def test_compare_identical_scalar_results() -> None:
    a = _result([[42]], columns=["n"])
    b = _result([[42]], columns=["count"])
    assert compare_results(a, b) == 1.0


def test_compare_disagreeing_scalar_results() -> None:
    a = _result([[42]], columns=["n"])
    b = _result([[7]], columns=["n"])
    assert compare_results(a, b) == 0.0


def test_compare_set_agreement() -> None:
    a = _result([[1], [2], [3]])
    b = _result([[3], [1], [2]])
    assert compare_results(a, b) == 1.0


def test_compare_partial_set_overlap_uses_jaccard() -> None:
    a = _result([[1], [2], [3]])
    b = _result([[2], [3], [4]])
    # |A ∩ B| / |A ∪ B| = 2/4 = 0.5
    assert abs(compare_results(a, b) - 0.5) < 1e-9


# ---------- confidence ---------- #
def test_schema_coverage_handles_overlap() -> None:
    assert schema_coverage_score(["customers"], ["customers", "orders"]) > 0
    assert schema_coverage_score([], ["customers"]) == 0
    assert schema_coverage_score(["x"], []) == 0.5


def test_compute_confidence_combines_signals() -> None:
    rep = _result([[1]])
    sanity = check_results(rep)
    score = compute_confidence(
        syntax_ok=True,
        back_translation=None,
        sanity=sanity,
        multi_query=None,
        tables_used=["customers"],
        candidate_tables=["customers", "orders"],
    )
    assert 0.0 <= score.composite <= 1.0
    assert score.syntax_validity == 1.0
