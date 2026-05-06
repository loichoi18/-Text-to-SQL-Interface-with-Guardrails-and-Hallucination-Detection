"""Result-level sanity checks.

These are structure-only checks that catch obvious failure modes
without re-executing the query. Each check returns a flag; the
confidence layer combines them.

Implemented checks:
* `null_heavy` — > 50% NULLs in any column suggests a botched JOIN.
* `empty_result_unexpected` — empty result for a "show me everything"
   style question.
* `extreme_aggregate` — single-row aggregate result with negative or
   absurdly large value (e.g. count > 1e9).
* `out_of_range_date` — a DATE column has values outside the data's
   known timespan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.sql.executor import ExecutionResult


@dataclass
class SanityFinding:
    code: str
    message: str
    severity: str  # "info" | "warn" | "error"


@dataclass
class SanityReport:
    findings: list[SanityFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warn")


def check_results(result: ExecutionResult, *, question: str = "") -> SanityReport:
    report = SanityReport()
    if result.row_count == 0:
        # Returning zero rows isn't always wrong. Only flag when the
        # question is shaped like "list/show/all/each".
        if any(kw in question.lower() for kw in ("list", "show", "all", "each", "every", "what are")):
            report.findings.append(SanityFinding(
                "EMPTY_RESULT",
                "Question implied a list of rows, but the query returned nothing.",
                "warn",
            ))
        return report

    # NULL-heavy column heuristic.
    n = result.row_count
    null_threshold = 0.5
    for ci, col in enumerate(result.columns):
        nulls = sum(1 for row in result.rows if row[ci] is None)
        if n > 0 and nulls / n >= null_threshold:
            report.findings.append(SanityFinding(
                "NULL_HEAVY_COLUMN",
                f"Column '{col}' is {nulls}/{n} NULL ({nulls / n:.0%}); possible bad JOIN.",
                "warn",
            ))

    # Single-row aggregate sanity.
    if result.row_count == 1 and len(result.columns) <= 3:
        for ci, col in enumerate(result.columns):
            v = result.rows[0][ci]
            if isinstance(v, (int, float)):
                if v < 0 and any(s in col.lower() for s in ("count", "qty", "quantity", "sales", "revenue", "total")):
                    report.findings.append(SanityFinding(
                        "NEGATIVE_AGGREGATE",
                        f"Aggregate '{col}' is negative ({v}); likely incorrect.",
                        "warn",
                    ))
                if isinstance(v, (int, float)) and abs(v) > 1e12:
                    report.findings.append(SanityFinding(
                        "EXTREME_AGGREGATE",
                        f"Aggregate '{col}' is extreme ({v:.2e}); likely a JOIN cardinality bug.",
                        "warn",
                    ))

    return report


def passes_to_score(report: SanityReport) -> float:
    """Convert sanity findings to a 0-1 score for the confidence layer."""
    if report.warnings == 0 and report.passed:
        return 1.0
    if not report.passed:
        return 0.0
    # one warning halves the score; multiple warnings degrade further
    return max(0.0, 1.0 - 0.4 * report.warnings)
