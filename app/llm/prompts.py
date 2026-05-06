"""Prompt assembly for the Text-to-SQL pipeline.

All prompts live here so they can be reviewed in one place — prompts
are part of the safety surface area as much as guardrail code.
"""
from __future__ import annotations

from textwrap import dedent

from app.schema.introspector import SchemaInfo, render_schema_for_prompt


SYSTEM_PROMPT = dedent("""\
    You are an expert SQL analyst working against a PostgreSQL database.

    Rules you must follow without exception:
    1. Only emit a single SELECT (or WITH ... SELECT) statement. Never DDL,
       never DML (no INSERT/UPDATE/DELETE/MERGE), never multiple statements.
    2. Use only tables and columns that appear in the schema given to you.
       If the question cannot be answered with the given schema, say so in
       the explanation and emit `SELECT 1 WHERE FALSE` as the SQL.
    3. Always include an explicit LIMIT (default to 1000) unless the
       question asks for a single aggregate value.
    4. Use ANSI / PostgreSQL syntax. Quote identifiers only when needed.
    5. Prefer descriptive column aliases. Round monetary calculations to 2
       decimal places.
    6. If a term in the question is genuinely ambiguous (e.g. "revenue"
       could mean gross or net), set `is_ambiguous=true` and list the
       possible interpretations rather than guessing.

    Return your answer using the structured output schema you've been
    given. Do not wrap SQL in markdown fences. Do not include trailing
    semicolons.
""")


# A handful of curated few-shot examples specific to the e-commerce
# schema. Real production code would pull these from a feedback store
# (correct examples submitted by users) — see the /v1/feedback endpoint.
FEW_SHOTS = [
    {
        "question": "How many customers do we have in each country?",
        "sql": "SELECT country, COUNT(*) AS customer_count FROM customers GROUP BY country ORDER BY customer_count DESC LIMIT 1000",
        "explanation": "Counts rows in the customers table grouped by country.",
        "tables": ["customers"],
    },
    {
        "question": "What was the total net revenue last month?",
        "sql": (
            "SELECT ROUND(SUM(quantity * unit_price - discount), 2) AS net_revenue "
            "FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
            "WHERE o.status NOT IN ('cancelled','refunded') "
            "AND o.order_date >= date_trunc('month', CURRENT_DATE - INTERVAL '1 month') "
            "AND o.order_date <  date_trunc('month', CURRENT_DATE)"
        ),
        "explanation": (
            "Net revenue is line-level (qty * unit_price - discount), summed across "
            "order_items joined to orders. Cancelled and refunded orders are excluded. "
            "Time filter targets the previous calendar month."
        ),
        "tables": ["orders", "order_items"],
    },
    {
        "question": "Top 5 best-selling products by units sold in 2025.",
        "sql": (
            "SELECT p.product_id, p.name, SUM(oi.quantity) AS units_sold "
            "FROM order_items oi "
            "JOIN orders o   ON o.order_id   = oi.order_id "
            "JOIN products p ON p.product_id = oi.product_id "
            "WHERE o.order_date >= DATE '2025-01-01' "
            "AND o.order_date <  DATE '2026-01-01' "
            "AND o.status NOT IN ('cancelled','refunded') "
            "GROUP BY p.product_id, p.name "
            "ORDER BY units_sold DESC "
            "LIMIT 5"
        ),
        "explanation": "Joins items to orders to filter by date and status, then sums quantities by product.",
        "tables": ["order_items", "orders", "products"],
    },
    {
        "question": "Drop the customers table.",
        "sql": "SELECT 1 WHERE FALSE",
        "explanation": "Refused: destructive operations (DROP) are not allowed by the system. The query was replaced with a no-op.",
        "tables": [],
    },
]


def build_generation_prompt(question: str, schema: SchemaInfo, table_subset: list[str] | None = None) -> str:
    """Assemble the full user-prompt body fed to the LLM."""
    schema_block = render_schema_for_prompt(schema, table_subset)
    examples_block = "\n\n".join(
        dedent(f"""\
        Example {i + 1}
        Q: {ex['question']}
        SQL: {ex['sql']}
        Explanation: {ex['explanation']}
        """).strip()
        for i, ex in enumerate(FEW_SHOTS)
    )
    return dedent(f"""\
        # Database schema
        {schema_block}

        # Few-shot examples
        {examples_block}

        # Question
        {question}

        Now produce the structured response.
    """)


# --- Back-translation prompt (used by the hallucination detector) ---
BACK_TRANSLATE_SYSTEM = dedent("""\
    You are an expert SQL analyst. Given a SQL query and the database
    schema it runs against, describe in one sentence what question the
    SQL answers. Be specific about filters, aggregations, and groupings.
    Return only the question text — no preamble.
""")


def build_back_translation_prompt(sql: str, schema: SchemaInfo, table_subset: list[str] | None = None) -> str:
    schema_block = render_schema_for_prompt(schema, table_subset)
    return dedent(f"""\
        # Database schema
        {schema_block}

        # SQL
        {sql}

        # Task
        In one sentence, what question does this SQL answer?
    """)


# --- Multi-query prompt: ask for an alternative independent solution ---
ALT_SOLUTION_SYSTEM = dedent("""\
    You are an expert SQL analyst. You will be given a question and the
    schema. Produce a SQL query that answers it using a DIFFERENT
    structural approach from a normal solution — e.g. a CTE instead of
    a subquery, window functions instead of GROUP BY, a different join
    order, or aggregating in a different table. The SAME ANSWER should
    come back, just by a different route. Same hard rules apply: only
    SELECT, only the given schema, always include LIMIT.
""")
