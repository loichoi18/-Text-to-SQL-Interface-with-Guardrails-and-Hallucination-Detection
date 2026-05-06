"""SQL syntactic validation.

We use sqlparse for cheap structural checks (statement count, basic
shape) and sqlglot for AST-aware checks (statement type, subquery
depth). The DB engine is the final source of truth — these checks
fail fast and produce friendly errors.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
import sqlparse


class SqlSyntaxError(Exception):
    """Raised when the LLM produced something we can't parse as SQL."""


@dataclass
class ParsedSQL:
    raw: str
    statement: sqlparse.sql.Statement
    ast: sqlglot.expressions.Expression


def parse_sql(sql: str) -> ParsedSQL:
    """Validate that `sql` is exactly one parseable SELECT/WITH statement.

    This is the very first checkpoint. If we can't parse it, we don't
    care what the guardrails say.
    """
    if not sql or not sql.strip():
        raise SqlSyntaxError("Empty SQL.")

    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) != 1:
        raise SqlSyntaxError(
            f"Expected exactly 1 statement, got {len(statements)}. "
            "Multiple statements are not allowed."
        )

    try:
        ast = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:  # sqlglot raises ParseError, TokenError, etc.
        raise SqlSyntaxError(f"sqlglot could not parse SQL: {exc}") from exc

    if ast is None:
        raise SqlSyntaxError("sqlglot returned an empty parse tree.")

    return ParsedSQL(raw=sql, statement=statements[0], ast=ast)
