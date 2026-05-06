"""Run the golden eval suite against a running API.

Metrics:
* `execution_match`     - Did the generated SQL produce the same result rows as the
                          expected SQL? (Order-insensitive set comparison; scalars
                          compared with float tolerance.)
* `exact_sql_match`     - Did the generated SQL string match the expected SQL after
                          whitespace normalisation? (Strict and informational only —
                          execution_match is the real metric.)
* `guardrail_blocked`   - For cases tagged `expects_block=true`, did the system
                          block the query?
* `hallucination_flag`  - For impossible/ambiguous prompts, did confidence drop
                          below 0.65 OR was the back-translation flagged?

Usage:
    # Make sure the API and Postgres are up:
    docker compose up -d
    python evals/run_evals.py
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).parent
GOLDEN_PATH = ROOT / "golden_dataset.json"
RESULTS_DIR = ROOT / "results"


def _read_only_engine() -> "create_engine":
    url = os.getenv(
        "EVAL_DATABASE_URL",
        "postgresql+psycopg2://readonly_user:readonly_pw@localhost:5432/shop",
    )
    return create_engine(url, future=True)


@dataclass
class Outcome:
    qid: str
    question: str
    category: str
    blocked: bool
    confidence: float
    similarity: float | None
    execution_match: bool | None
    exact_sql_match: bool
    guardrail_pass: bool | None
    hallucination_pass: bool | None
    notes: list[str] = field(default_factory=list)
    generated_sql: str = ""


def _normalise_sql(sql: str) -> str:
    return " ".join(sql.replace("\n", " ").split()).rstrip(";").lower()


def _run_expected(engine, sql: str) -> set[tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return {tuple(_norm(c) for c in r) for r in rows}


def _norm(v: Any) -> Any:
    import datetime as _dt
    import decimal

    if isinstance(v, decimal.Decimal):
        return round(float(v), 4)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _result_match(generated: dict[str, Any], expected: set[tuple]) -> bool:
    gen = {tuple(_norm(c) for c in r) for r in generated.get("rows", [])}
    if not expected and not gen:
        return True
    if len(expected) == 1 == len(gen):
        e_row = next(iter(expected))
        g_row = next(iter(gen))
        if len(e_row) == 1 == len(g_row):
            return e_row[0] == g_row[0] or _scalar_close(e_row[0], g_row[0])
    return gen == expected


def _scalar_close(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-3
    except (TypeError, ValueError):
        return False


def _post_query(api_base: str, question: str) -> dict[str, Any]:
    r = requests.post(
        f"{api_base}/v1/query",
        json={"question": question, "enable_multi_query": False, "enable_back_translation": True},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def evaluate(api_base: str) -> list[Outcome]:
    cases = json.loads(GOLDEN_PATH.read_text())
    engine = _read_only_engine()
    outcomes: list[Outcome] = []

    for case in cases:
        qid = case["id"]
        question = case["question"]
        category = case.get("category", "?")
        expected_sql = case.get("expected_sql")
        expects_block = bool(case.get("expects_block"))
        expected_block_rule = case.get("expected_block_rule")
        expects_ambiguous = bool(case.get("expects_ambiguous"))
        expects_no_data = bool(case.get("expects_no_data"))

        notes: list[str] = []
        try:
            resp = _post_query(api_base, question)
        except Exception as exc:
            outcomes.append(Outcome(
                qid=qid, question=question, category=category,
                blocked=False, confidence=0.0, similarity=None,
                execution_match=False, exact_sql_match=False,
                guardrail_pass=False if expects_block else None,
                hallucination_pass=False,
                notes=[f"API error: {exc}"],
            ))
            continue

        blocked = bool(resp.get("blocked"))
        confidence = float(resp.get("confidence", {}).get("composite", 0.0))
        bt = resp.get("back_translation") or {}
        similarity = bt.get("similarity")

        guardrail_pass: bool | None = None
        if expects_block:
            guardrail_pass = blocked
            if expected_block_rule and resp.get("block_reason"):
                guardrail_pass = blocked and resp["block_reason"]["rule"] == expected_block_rule

        execution_match: bool | None = None
        exact_match = False
        if expected_sql and not expects_block:
            try:
                expected_rows = _run_expected(engine, expected_sql)
                execution_match = _result_match(resp, expected_rows)
            except Exception as exc:
                notes.append(f"expected SQL failed to run: {exc}")
                execution_match = False
            exact_match = _normalise_sql(resp.get("sql", "")) == _normalise_sql(expected_sql)
        elif expects_block:
            # No SQL to compare; success is defined by guardrail_pass.
            execution_match = None

        # Hallucination signal: for impossible / ambiguous prompts we want
        # either low confidence OR explicit ambiguity flag.
        hallucination_pass: bool | None = None
        if expects_ambiguous:
            hallucination_pass = bool(resp.get("is_ambiguous")) or confidence < 0.65
        elif expects_no_data:
            hallucination_pass = (resp.get("row_count", 0) == 0) or confidence < 0.65
        elif not expects_block:
            hallucination_pass = (similarity is None) or (similarity >= 0.45)

        outcomes.append(Outcome(
            qid=qid, question=question, category=category,
            blocked=blocked, confidence=round(confidence, 3),
            similarity=similarity,
            execution_match=execution_match,
            exact_sql_match=exact_match,
            guardrail_pass=guardrail_pass,
            hallucination_pass=hallucination_pass,
            notes=notes,
            generated_sql=resp.get("sql", ""),
        ))

    return outcomes


def summarise(outcomes: list[Outcome]) -> dict[str, Any]:
    sql_cases   = [o for o in outcomes if o.execution_match is not None]
    block_cases = [o for o in outcomes if o.guardrail_pass is not None]
    halluc      = [o for o in outcomes if o.hallucination_pass is not None]

    return {
        "total":             len(outcomes),
        "execution_match":   {
            "passed": sum(1 for o in sql_cases if o.execution_match),
            "total":  len(sql_cases),
            "rate":   round(sum(1 for o in sql_cases if o.execution_match) / max(1, len(sql_cases)), 3),
        },
        "exact_sql_match":   {
            "passed": sum(1 for o in sql_cases if o.exact_sql_match),
            "total":  len(sql_cases),
            "rate":   round(sum(1 for o in sql_cases if o.exact_sql_match) / max(1, len(sql_cases)), 3),
        },
        "guardrail":         {
            "passed": sum(1 for o in block_cases if o.guardrail_pass),
            "total":  len(block_cases),
            "rate":   round(sum(1 for o in block_cases if o.guardrail_pass) / max(1, len(block_cases)), 3),
        },
        "hallucination":     {
            "passed": sum(1 for o in halluc if o.hallucination_pass),
            "total":  len(halluc),
            "rate":   round(sum(1 for o in halluc if o.hallucination_pass) / max(1, len(halluc)), 3),
        },
        "unsafe_executions": sum(
            1 for o in outcomes
            if (o.guardrail_pass is False) and not o.blocked
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--out", default=str(RESULTS_DIR / f"results-{int(time.time())}.json"))
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    outcomes = evaluate(args.api)
    summary  = summarise(outcomes)
    payload = {
        "summary":  summary,
        "outcomes": [o.__dict__ for o in outcomes],
        "api":      args.api,
        "generated_at": int(time.time()),
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))

    print(json.dumps(summary, indent=2))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
