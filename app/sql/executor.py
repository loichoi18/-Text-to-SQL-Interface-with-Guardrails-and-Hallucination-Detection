"""Sandboxed query execution layer.

Every guardrail-approved SELECT runs here. Responsibilities:
* Open a read-only transaction (defense-in-depth on top of the
  read-only DB user).
* Capture the EXPLAIN plan for auditability.
* Convert results to a JSON-serialisable structure.
* Time the query.
* Roll back, always.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db.session import readonly_connection
from app.sql.guardrails import (
    GuardrailResult,
    GuardrailViolation,
    apply_guardrails,
)


@dataclass
class ExecutionResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    execution_ms: float
    explain_plan: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rules_passed: list[str] = field(default_factory=list)


def run_safely(sql: str, *, capture_explain: bool = True) -> ExecutionResult:
    """Run guardrails + execute the query. Caller already received the
    LLM's SQL — this is the single chokepoint that touches the DB.
    """
    settings = get_settings()
    with readonly_connection() as conn:
        # Guardrails need a connection for EXPLAIN cost checks.
        guardrail = apply_guardrails(sql, explain_conn=conn)
        return _execute_validated(conn, guardrail, capture_explain=capture_explain)


def _execute_validated(conn, guardrail: GuardrailResult, *, capture_explain: bool) -> ExecutionResult:
    sql = guardrail.safe_sql
    explain_lines: list[str] = []
    if capture_explain:
        try:
            for r in conn.execute(text(f"EXPLAIN {sql}")):
                explain_lines.append(r[0])
        except Exception as exc:
            explain_lines = [f"<EXPLAIN failed: {exc}>"]

    start = time.perf_counter()
    result = conn.execute(text(sql))
    rows = result.fetchall()
    elapsed_ms = (time.perf_counter() - start) * 1000
    columns = list(result.keys())

    # SQLAlchemy returns Row objects; coerce to plain lists for JSON.
    materialised = [[_to_jsonable(v) for v in row] for row in rows]

    return ExecutionResult(
        sql=sql,
        columns=columns,
        rows=materialised,
        row_count=len(materialised),
        execution_ms=round(elapsed_ms, 2),
        explain_plan=explain_lines,
        warnings=guardrail.warnings,
        rules_passed=guardrail.rules_passed,
    )


def _to_jsonable(v: Any) -> Any:
    """Coerce DB values into JSON-friendly Python primitives."""
    import datetime as _dt
    import decimal

    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return str(v)


__all__ = ["ExecutionResult", "run_safely", "GuardrailViolation"]
