"""Guardrail middleware: block destructive operations BEFORE execution.

Layered defenses, top-down:
  1. Statement type check  — only SELECT / WITH allowed.
  2. Forbidden token sweep — DDL/DML keywords kill the query even if
     the parser misclassifies them.
  3. Subquery depth check — bounds prompt-engineered nesting attacks
     and accidental N^k blowups.
  4. LIMIT enforcement     — if the LLM forgot one, we add it.
  5. EXPLAIN cost gate     — refuse queries the planner thinks will
     scan more than max_explain_rows.

Every blocked query gets logged with the rule that fired so security
review can audit them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

import sqlglot
import sqlparse
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.sql.validator import ParsedSQL, SqlSyntaxError, parse_sql

log = logging.getLogger("guardrails")


# Tokens that should never appear in a generated query, regardless of
# what the parser says. Matched as whole words, case-insensitive.
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE",
    "VACUUM", "ANALYZE", "REINDEX", "CLUSTER",
    "COPY",  # Postgres COPY can read/write to the filesystem.
    "CALL", "DO",  # stored-proc invocation, anonymous code blocks.
)

# Allowed top-level statement types. sqlglot expression class names.
_ALLOWED_TOP_LEVEL = {"Select", "Subquery", "Union", "Intersect", "Except", "With"}


class GuardrailViolation(Exception):
    """Raised when a generated query fails any guardrail rule."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"[{rule}] {message}")
        self.rule = rule
        self.message = message


@dataclass
class GuardrailResult:
    """Result of running a query through the guardrails."""
    safe_sql: str
    parsed: ParsedSQL
    warnings: list[str] = field(default_factory=list)
    rules_passed: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Individual rules                                                      #
# --------------------------------------------------------------------- #
def _check_statement_type(parsed: ParsedSQL) -> None:
    expr_name = type(parsed.ast).__name__
    if expr_name not in _ALLOWED_TOP_LEVEL:
        raise GuardrailViolation(
            "STATEMENT_TYPE",
            f"Top-level statement must be SELECT/WITH/UNION; got {expr_name}.",
        )
    # If it's a CTE wrapper, the inner expression must also be a SELECT-shape.
    if expr_name == "With":
        inner = parsed.ast.this
        if type(inner).__name__ not in _ALLOWED_TOP_LEVEL:
            raise GuardrailViolation(
                "STATEMENT_TYPE",
                f"CTE body must be SELECT/UNION; got {type(inner).__name__}.",
            )


_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    return sqlparse.format(sql, strip_comments=True)


def _check_forbidden_keywords(parsed: ParsedSQL) -> None:
    sanitized = _strip_comments(parsed.raw)
    match = _FORBIDDEN_RE.search(sanitized)
    if match:
        raise GuardrailViolation(
            "FORBIDDEN_KEYWORD",
            f"Query contains forbidden keyword: {match.group(0).upper()}.",
        )


def _max_subquery_depth(expr: sqlglot.expressions.Expression) -> int:
    """Return the maximum SELECT nesting depth in `expr`.

    A bare `SELECT ... FROM t` has depth 1. A query with one subquery
    has depth 2, etc.
    """
    def walk(node: sqlglot.expressions.Expression, depth_so_far: int) -> int:
        is_select = isinstance(node, sqlglot.expressions.Select)
        new_depth = depth_so_far + 1 if is_select else depth_so_far
        max_child = new_depth
        for child in node.args.values():
            children = child if isinstance(child, list) else [child]
            for c in children:
                if isinstance(c, sqlglot.expressions.Expression):
                    max_child = max(max_child, walk(c, new_depth))
        return max_child

    return walk(expr, 0)


def _check_subquery_depth(parsed: ParsedSQL, *, limit: int) -> None:
    depth = _max_subquery_depth(parsed.ast)
    if depth > limit:
        raise GuardrailViolation(
            "SUBQUERY_DEPTH",
            f"Subquery depth {depth} exceeds limit {limit}.",
        )


def _enforce_limit(parsed: ParsedSQL, *, max_rows: int) -> tuple[str, list[str]]:
    """Add a LIMIT to the outer SELECT if missing or oversized."""
    expr = parsed.ast
    warnings: list[str] = []

    # Find the outer SELECT we should attach LIMIT to. For SET ops
    # (UNION/INTERSECT/EXCEPT) sqlglot lets us call .limit() on the
    # whole thing.
    target = expr
    if isinstance(expr, sqlglot.expressions.With):
        target = expr.this  # the SELECT/UNION inside the WITH

    existing = target.args.get("limit") if hasattr(target, "args") else None

    if existing is None:
        new_expr = expr.copy()
        if isinstance(new_expr, sqlglot.expressions.With):
            new_expr.set("this", new_expr.this.limit(max_rows))
        else:
            new_expr = new_expr.limit(max_rows)
        warnings.append(f"No LIMIT in query; added LIMIT {max_rows}.")
        return new_expr.sql(dialect="postgres"), warnings

    # If the LLM specified a literal LIMIT > max_rows, clamp it.
    try:
        limit_expr = existing.expression
        if isinstance(limit_expr, sqlglot.expressions.Literal) and limit_expr.is_int:
            requested = int(limit_expr.this)
            if requested > max_rows:
                new_expr = expr.copy()
                if isinstance(new_expr, sqlglot.expressions.With):
                    new_expr.set("this", new_expr.this.limit(max_rows))
                else:
                    new_expr = new_expr.limit(max_rows)
                warnings.append(f"LIMIT {requested} clamped to {max_rows}.")
                return new_expr.sql(dialect="postgres"), warnings
    except Exception:
        # Be conservative: if we can't read the LIMIT, leave it alone.
        pass

    return parsed.raw, warnings


def _check_explain_cost(conn: Connection, sql: str, *, max_rows: int) -> int:
    """Run EXPLAIN and refuse if the planner expects too many rows.

    Returns the planner's row estimate. Raises GuardrailViolation if
    the estimate exceeds `max_rows`.
    """
    try:
        rows = conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}")).fetchall()
    except Exception as exc:
        # If EXPLAIN itself fails, this is suspicious — the underlying
        # query is also going to fail. Surface as a violation so we
        # don't waste time running it.
        raise GuardrailViolation("EXPLAIN_FAILED", f"EXPLAIN failed: {exc}") from exc

    plan_rows = 0
    for r in rows:
        plan = r[0]
        if isinstance(plan, list) and plan:
            plan = plan[0]
        if isinstance(plan, dict) and "Plan" in plan:
            plan_rows = int(plan["Plan"].get("Plan Rows", 0))
            break

    if plan_rows > max_rows:
        raise GuardrailViolation(
            "EXPLAIN_ROWS",
            f"Planner estimates {plan_rows:,} rows; limit is {max_rows:,}.",
        )
    return plan_rows


# --------------------------------------------------------------------- #
# Public entry point                                                    #
# --------------------------------------------------------------------- #
def apply_guardrails(
    sql: str,
    *,
    explain_conn: Connection | None = None,
    extra_rules: Iterable[callable] = (),
) -> GuardrailResult:
    """Run all guardrails on `sql`.

    `explain_conn` is the read-only DB connection to use for the EXPLAIN
    cost check. Pass None to skip it (used in unit tests).
    """
    s = get_settings()
    parsed = parse_sql(sql)
    rules_passed: list[str] = []
    warnings: list[str] = []

    _check_statement_type(parsed);   rules_passed.append("STATEMENT_TYPE")
    _check_forbidden_keywords(parsed); rules_passed.append("FORBIDDEN_KEYWORD")
    _check_subquery_depth(parsed, limit=s.max_subquery_depth)
    rules_passed.append("SUBQUERY_DEPTH")

    safe_sql, lim_warnings = _enforce_limit(parsed, max_rows=s.max_rows)
    warnings.extend(lim_warnings)
    rules_passed.append("LIMIT_ENFORCED")

    if explain_conn is not None:
        _check_explain_cost(explain_conn, safe_sql, max_rows=s.max_explain_rows)
        rules_passed.append("EXPLAIN_ROWS")

    for rule in extra_rules:
        rule(parsed)

    log.info("guardrails passed", extra={
        "rules_passed": rules_passed,
        "warnings": warnings,
    })
    return GuardrailResult(safe_sql=safe_sql, parsed=parsed, warnings=warnings, rules_passed=rules_passed)


def explain_violation(exc: GuardrailViolation) -> str:
    """Human-readable explanation for the API response."""
    return f"Blocked by guardrail rule {exc.rule}: {exc.message}"


__all__ = [
    "GuardrailViolation",
    "GuardrailResult",
    "apply_guardrails",
    "explain_violation",
    "FORBIDDEN_KEYWORDS",
    "SqlSyntaxError",
]
