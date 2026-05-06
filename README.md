# Text-to-SQL Interface with Guardrails and Hallucination Detection

> **Production-grade text-to-SQL.** Plain-English questions become safe,
> guardrail-checked SQL, executed read-only against PostgreSQL, then
> validated by a multi-signal hallucination detector and returned with
> a confidence breakdown.

The bar isn't *"can an LLM generate SQL?"* (every demo can do that) but
*"would compliance approve this for production?"* — every generated
query has to be safe, auditable, and verifiable.

---

## Why this project

* **Safety first.** A guardrail layer rejects every destructive operation
  before it touches the database, and a SELECT-only Postgres user is
  used as a second line of defense. No DDL, no DML, no surprises.
* **Hallucination detection, not just generation.** A back-translation
  alignment check, sanity heuristics, and a multi-query agreement
  check combine into a single confidence score the user can trust.
* **Auditability.** Every blocked query is logged with the rule that
  fired. The EXPLAIN plan and the alternative SQL are both returned.
* **Real engine, not a toy.** PostgreSQL via Docker; SQLAlchemy schema
  introspection picks up comments, FKs, and sample values automatically.

---

## Architecture

```
                            +----------------------+
   user question  ─────────►|  FastAPI /v1/query   |
                            +----------+-----------+
                                       │
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                        ▼
      Schema introspection      LLM SQL generation        LLM alternative SQL
      (SQLAlchemy + pg_class)   (Claude, structured       (different approach;
                                 tool-use output)          used for agreement)
              │                        │                        │
              └────────────────────────┴───── prompt ───────────┘
                                       │
                                       ▼
                          Guardrails (parser + AST):
                          - statement type check
                          - forbidden keyword sweep
                          - subquery depth cap
                          - LIMIT enforcement / clamp
                          - EXPLAIN row-cost gate
                                       │
                                       ▼
                          Read-only transaction
                          (read-only DB user, statement_timeout)
                                       │
                                       ▼
                         ┌──────── result ────────┐
                         ▼                        ▼
                Hallucination detection      Sanity checks
                (back-translate + score,     (NULL-heavy cols,
                 multi-query agreement)      negative aggregates,
                                             empty result for "list X")
                         │                        │
                         └──────── confidence ────┘
                                       │
                                       ▼
                              JSON response
                              { sql, rows, confidence,
                                back_translation, multi_query,
                                sanity_findings, blocked }
```

---

## Tech stack

| Component        | Choice                              | Why                                    |
| ---------------- | ----------------------------------- | -------------------------------------- |
| Language         | Python 3.11+                        | Standard for data tooling              |
| LLM              | Anthropic Claude Sonnet (tool-use)  | Strong structured output               |
| Database         | PostgreSQL 16 (Docker)              | Real SQL engine, not SQLite            |
| ORM / reflection | SQLAlchemy 2.0                      | Schema introspection, drivers          |
| SQL parsing      | sqlparse + sqlglot                  | Lexer + AST checks for guardrails      |
| Embeddings       | sentence-transformers (MiniLM-L6)   | Schema relevance + back-translation    |
| API              | FastAPI                             | Production-grade serving               |
| Frontend         | Streamlit                           | Single-file, polished demo UI          |
| Tests            | pytest                              | 35 unit tests, no DB required          |
| Container        | Docker + docker-compose             | DB + API + UI orchestrated             |

---

## Repository layout

```
text-to-sql-guardrails/
├── app/                        FastAPI service
│   ├── main.py                 app factory + logging
│   ├── config.py               pydantic-settings (env vars)
│   ├── llm/                    Anthropic client + prompts
│   ├── schema/                 introspection + embedding-based filter
│   ├── sql/                    generator, validator, guardrails, executor
│   ├── validation/             back-translation, sanity, multi-query, confidence
│   ├── db/session.py           lazy engine wiring + read-only context
│   └── api/                    routes + pydantic models
├── frontend/streamlit_app.py   demo UI (question → SQL → results → confidence)
├── seed/
│   ├── 01_schema.sql           e-commerce DDL
│   ├── 02_seed_data.sql        deterministic seed (60 cust, 30 prod, 240 orders)
│   ├── 03_readonly_user.sql    SELECT-only Postgres role
│   └── generate_seed.py        regenerate 02_seed_data.sql
├── evals/
│   ├── golden_dataset.json     52 NL → SQL test cases
│   └── run_evals.py            eval runner + metrics
├── tests/                      pytest unit tests (no DB needed)
├── docker-compose.yml          db + api + frontend
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Quick start

### 1. Clone and configure

```bash
git clone <your-fork>
cd text-to-sql-guardrails
cp .env.example .env
# Open .env and set ANTHROPIC_API_KEY
```

### 2. Bring up the stack

```bash
docker compose up --build -d
```

This starts:

* `db` — PostgreSQL 16, seeded with the e-commerce schema and 240 orders
  worth of sample data on first boot.
* `api` — FastAPI on `http://localhost:8000` (Swagger at `/docs`).
* `frontend` — Streamlit on `http://localhost:8501`.

### 3. Try a question

Open `http://localhost:8501` and ask:

* *"How many customers do we have in each country?"*
* *"Top 5 best-selling products by units sold in 2025."*
* *"What was the total net revenue last month?"*
* *"Drop the customers table."* — to watch the guardrail fire.

### 4. Run the evals

```bash
docker compose exec api python evals/run_evals.py
```

You'll see a JSON summary like:

```json
{
  "execution_match":  {"passed": 38, "total": 42, "rate": 0.905},
  "exact_sql_match":  {"passed": 22, "total": 42, "rate": 0.524},
  "guardrail":        {"passed":  6, "total":  6, "rate": 1.000},
  "hallucination":    {"passed": 48, "total": 50, "rate": 0.960},
  "unsafe_executions": 0
}
```

### Local dev (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# In one terminal — start Postgres any way you like, then:
psql $DATABASE_URL -f seed/01_schema.sql
psql $DATABASE_URL -f seed/02_seed_data.sql
psql $DATABASE_URL -f seed/03_readonly_user.sql

# Run the API
uvicorn app.main:app --reload

# In another terminal — run the UI
streamlit run frontend/streamlit_app.py
```

---

## How safety works

Every generated query passes through five guardrail rules **before
execution**:

| Rule                | What it does                                                    |
| ------------------- | --------------------------------------------------------------- |
| `STATEMENT_TYPE`    | Parses with sqlglot; rejects anything that isn't SELECT/WITH/UNION. |
| `FORBIDDEN_KEYWORD` | Whole-word regex sweep for INSERT/UPDATE/DELETE/DROP/ALTER/etc. |
| `SUBQUERY_DEPTH`    | Bounds nesting (default 3) so prompt-engineered blow-ups die.   |
| `LIMIT_ENFORCED`    | Adds or clamps `LIMIT` to `MAX_ROWS` (default 1000).            |
| `EXPLAIN_ROWS`      | Refuses queries the planner expects to scan more than 1M rows.  |

Layered on top:

* All queries run through a Postgres user with **SELECT-only** privileges.
* Each query runs inside a `READ ONLY` transaction with a short
  `statement_timeout`, and is rolled back unconditionally afterwards.
* Every blocked query is logged with the rule that fired and the SQL
  text — auditable.

See `app/sql/guardrails.py` for the full rule list and tests in
`tests/test_guardrails.py`.

---

## How hallucination detection works

Three independent signals get rolled into one confidence score:

1. **Back-translation alignment.** After generating the SQL, the model
   is asked *"What question does this SQL answer?"* The cosine
   similarity between that and the original question gates the score.
2. **Result sanity checks.** NULL-heavy columns suggest a botched JOIN.
   Negative or absurdly large aggregates are flagged. Empty results
   for *"list / show / each"* questions are flagged.
3. **Multi-query agreement.** A second SQL is generated using a
   structurally different approach (CTE vs subquery, different join
   order). Both run; results are compared. Disagreement is the
   strongest hallucination signal we have.

The composite score is a weighted average:

```python
WEIGHTS = {
    "syntax_validity":  0.10,
    "back_translation": 0.30,
    "sanity_score":     0.20,
    "multi_query":      0.30,
    "schema_coverage":  0.10,
}
```

The UI displays the breakdown so users can see *why* confidence is
high or low — not just the number.

---

## Running tests

```bash
pytest tests/ -v
```

The unit tests do not require Postgres or an LLM API key — they run
the guardrails, sanity checks, and schema introspection against an
in-memory SQLite database. 35 tests cover:

* All 10 forbidden-keyword categories (DDL + DML)
* CTE / subquery depth boundaries
* LIMIT enforcement and clamping
* Multi-statement and empty SQL rejection
* NULL-heavy column / negative aggregate sanity heuristics
* Multi-query agreement scoring (scalar, set-equal, Jaccard partial)
* Confidence weighting math

---

## API reference

```
POST /v1/query           NL question → SQL + results + confidence
GET  /v1/schema          structured database schema
GET  /v1/history         last 50 queries (in-memory)
POST /v1/feedback        mark a result correct/incorrect
GET  /v1/health          liveness probe
GET  /docs               Swagger UI
```

Example:

```bash
curl -s http://localhost:8000/v1/query \
  -H 'content-type: application/json' \
  -d '{"question":"Top 3 customers by total spend in 2025"}' | jq
```

---

## Demo script (for portfolio recording)

Under 4 minutes:

1. **Ask a normal question** — *"Top 5 best-selling products by units
   sold in 2025."* Show the SQL, the result table, the back-translation,
   the multi-query agreement (both 1.0), and the green confidence badge.
2. **Trigger an ambiguous question** — *"What is the revenue?"* Show
   the structured clarification with multiple interpretations.
3. **Trigger a guardrail** — *"Drop the customers table."* Show the
   `FORBIDDEN_KEYWORD` block and the audit-log entry.
4. **Force a disagreement** — ask a question whose join structure is
   subtly wrong, and watch the multi-query agreement drop and the
   confidence go yellow.
5. **Run the eval suite** in the terminal — recite the headline numbers.

---

## Roadmap

* Persistent feedback store (Postgres) feeding back into few-shot examples.
* Cost telemetry per query (LLM tokens + DB time).
* Pluggable LLM backends (OpenAI, Bedrock).
* Schema-aware autocompletion in the editable SQL field.

## License

MIT
