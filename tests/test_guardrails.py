"""Guardrail unit tests.

These run without a live database — they exercise the parser/AST
checks only. The EXPLAIN cost gate has its own integration test
that requires Postgres.
"""
from __future__ import annotations

import pytest

from app.sql.guardrails import (
    GuardrailViolation,
    apply_guardrails,
)
from app.sql.validator import SqlSyntaxError


# --------------------------------------------------------------------- #
# Happy paths                                                           #
# --------------------------------------------------------------------- #
def test_basic_select_passes() -> None:
    res = apply_guardrails("SELECT * FROM customers LIMIT 50")
    assert "STATEMENT_TYPE" in res.rules_passed
    assert "FORBIDDEN_KEYWORD" in res.rules_passed
    assert "SUBQUERY_DEPTH" in res.rules_passed
    assert "LIMIT_ENFORCED" in res.rules_passed
    assert "limit 50" in res.safe_sql.lower()


def test_cte_passes() -> None:
    sql = """
        WITH recent AS (SELECT * FROM orders WHERE order_date >= DATE '2025-01-01')
        SELECT COUNT(*) FROM recent LIMIT 10
    """
    res = apply_guardrails(sql)
    assert "STATEMENT_TYPE" in res.rules_passed


def test_missing_limit_is_added() -> None:
    res = apply_guardrails("SELECT * FROM customers")
    assert "limit" in res.safe_sql.lower()
    assert any("LIMIT" in w for w in res.warnings)


def test_oversized_limit_is_clamped() -> None:
    res = apply_guardrails("SELECT * FROM customers LIMIT 1000000")
    assert "limit 1000" in res.safe_sql.lower()
    assert any("clamped" in w.lower() for w in res.warnings)


# --------------------------------------------------------------------- #
# Forbidden keywords                                                    #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM customers",
    "UPDATE customers SET email = 'x'",
    "INSERT INTO customers VALUES (1)",
    "TRUNCATE TABLE order_items",
    "ALTER TABLE orders ADD COLUMN x INT",
    "GRANT SELECT ON customers TO public",
    "REVOKE ALL ON customers FROM readonly_user",
    "VACUUM customers",
    "COPY customers TO '/tmp/dump.csv'",
])
def test_destructive_statements_blocked(sql: str) -> None:
    with pytest.raises((GuardrailViolation, SqlSyntaxError)):
        apply_guardrails(sql)


def test_select_with_embedded_drop_in_string_is_blocked_for_safety() -> None:
    # Conservative: even a literal that looks like DDL trips the
    # forbidden-keyword sweep. We prefer false positives here over
    # the hypothetical case where bypass works.
    with pytest.raises(GuardrailViolation) as info:
        apply_guardrails("SELECT 'DROP TABLE customers' AS x")
    assert info.value.rule == "FORBIDDEN_KEYWORD"


# --------------------------------------------------------------------- #
# Multi-statement / syntax                                              #
# --------------------------------------------------------------------- #
def test_multiple_statements_rejected() -> None:
    with pytest.raises(SqlSyntaxError):
        apply_guardrails("SELECT 1; SELECT 2")


def test_empty_sql_rejected() -> None:
    with pytest.raises(SqlSyntaxError):
        apply_guardrails("")


def test_garbage_rejected() -> None:
    with pytest.raises((SqlSyntaxError, GuardrailViolation)):
        apply_guardrails("not even close to sql")


# --------------------------------------------------------------------- #
# Subquery depth                                                        #
# --------------------------------------------------------------------- #
def test_subquery_depth_within_limit_passes() -> None:
    sql = """
        SELECT *
        FROM (SELECT customer_id FROM (SELECT * FROM customers) inner1) outer1
        LIMIT 10
    """
    apply_guardrails(sql)  # depth 3, default limit 3 — OK


def test_subquery_depth_over_limit_blocked() -> None:
    sql = """
        SELECT *
        FROM (
            SELECT *
            FROM (
                SELECT *
                FROM (
                    SELECT * FROM customers
                ) a
            ) b
        ) c
        LIMIT 10
    """
    with pytest.raises(GuardrailViolation) as info:
        apply_guardrails(sql)
    assert info.value.rule == "SUBQUERY_DEPTH"
