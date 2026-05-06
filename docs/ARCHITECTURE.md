# Architecture Notes

This file goes one level deeper than the README, for reviewers who want
to understand each module's responsibility.

## End-to-end request flow

```
1. /v1/query receives { question }
2. Schema introspection (cached) → list of all tables + columns
3. Schema relevance filter (sentence-transformers cosine):
     - keep top-K tables OR all tables if schema is small
4. Prompt construction:
     SYSTEM_PROMPT + filtered schema + few-shot examples + question
5. LLM generates structured output via Anthropic tool-use:
     { sql, explanation, confidence, tables_used, columns_used,
       is_ambiguous, interpretations }
6. SQL passes through guardrails:
     parse_sql() → STATEMENT_TYPE → FORBIDDEN_KEYWORD → SUBQUERY_DEPTH
     → LIMIT_ENFORCED → EXPLAIN_ROWS
   Any failure short-circuits with a structured BlockedResponse.
7. Validated SQL runs inside a READ ONLY transaction against the
   readonly_user role. Statement timeout enforced. Always rolled back.
8. Hallucination detection:
     a. Back-translation: SQL → natural-language question, similarity
        scored against the original.
     b. Sanity checks: NULL-heavy / negative aggregate / extreme value /
        empty list-style result.
     c. Multi-query: ask LLM for a structurally different SQL (CTE vs
        subquery, different join order). Run both. Compare result sets.
9. Confidence aggregation: weighted sum of the five signals.
10. JSON response, with full audit info: SQL, EXPLAIN plan, alternative
    SQL, back-translation, sanity findings, blocked rule (if any),
    confidence breakdown.
```

## Module responsibilities

* `app/config.py` — single typed settings object (pydantic-settings).
* `app/db/session.py` — lazy engine wiring; read-only transaction
  context manager; never imports SQLAlchemy connections at module load.
* `app/schema/introspector.py` — SchemaInfo dataclass, sample-value
  enrichment, foreign keys, comments. Dialect-aware (Postgres + SQLite).
* `app/schema/filter.py` — embedding-based table relevance filter.
* `app/llm/prompts.py` — every prompt the system emits, in one place
  (prompts are part of the safety surface area).
* `app/llm/client.py` — Anthropic wrapper with tool-use structured
  output and a free-text variant for back-translation.
* `app/sql/validator.py` — sqlparse + sqlglot parse step.
* `app/sql/guardrails.py` — the rule pipeline, including AST-aware
  subquery depth check and LIMIT enforcement that respects WITH clauses.
* `app/sql/executor.py` — single chokepoint that touches the database.
* `app/sql/generator.py` — high-level wrapper that combines prompt +
  client into a typed result.
* `app/validation/back_translation.py` — SQL → question + similarity.
* `app/validation/sanity.py` — result-level heuristics.
* `app/validation/multi_query.py` — alternative-SQL agreement.
* `app/validation/confidence.py` — weighted aggregation.
* `app/api/models.py` — every wire-level pydantic model.
* `app/api/routes.py` — the orchestrator. Deliberately linear.

## Threat model + mitigations

| Threat                                            | Mitigation                                |
| ------------------------------------------------- | ----------------------------------------- |
| LLM emits DROP/DELETE                             | `FORBIDDEN_KEYWORD` regex over stripped SQL. |
| LLM smuggles DDL inside a CTE                     | sqlglot AST inspects nested expressions. |
| LLM emits multi-statement SQL                     | sqlparse statement count check.           |
| LLM emits a query that scans a billion rows       | EXPLAIN row-cost gate.                    |
| Guardrail bypassed (regex evaded somehow)         | DB user has SELECT-only privileges.       |
| Query has unintended side effect (function call)  | Read-only transaction, always rolled back.|
| Long-running query                                | `statement_timeout` set at session scope. |

## Why these guardrails are not enough on their own

Defense-in-depth: guardrails are the application layer, the read-only
DB user is the platform layer, and the read-only transaction with a
statement timeout is the runtime layer. Each layer assumes the layer
above might fail.
