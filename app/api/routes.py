"""HTTP routes.

The orchestration logic for a single /v1/query call lives here. It is
deliberately linear and readable so that auditors can follow the
control flow end-to-end without jumping through layers.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.models import (
    BackTranslationResponse,
    ConfidenceResponse,
    FeedbackRequest,
    GuardrailWarning,
    HistoryItem,
    HistoryResponse,
    MultiQueryResponse,
    QueryRequest,
    QueryResponse,
    SanityFindingResponse,
    SchemaResponse,
    TableSummary,
)
from app.db.session import _get_admin_engine
from app.llm.client import LLMClient
from app.schema.filter import filter_relevant_tables
from app.schema.introspector import SchemaInfo, introspect
from app.sql.executor import ExecutionResult, run_safely
from app.sql.generator import generate_alternative_sql, generate_sql
from app.sql.guardrails import GuardrailViolation
from app.sql.validator import SqlSyntaxError
from app.validation.back_translation import back_translate_and_score
from app.validation.confidence import compute_confidence
from app.validation.multi_query import run_multi_query
from app.validation.sanity import check_results

log = logging.getLogger("api")

router = APIRouter(prefix="/v1")

_HISTORY: list[dict[str, Any]] = []
_FEEDBACK: list[dict[str, Any]] = []
_SCHEMA_CACHE: SchemaInfo | None = None


def _get_schema() -> SchemaInfo:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = introspect(_get_admin_engine())
    return _SCHEMA_CACHE


def _get_llm() -> LLMClient:
    return LLMClient.from_settings()


def _to_back_translation_dataclass(bt: BackTranslationResponse):
    from app.validation.back_translation import BackTranslationResult
    return BackTranslationResult(
        original_question="",
        back_translation=bt.back_translation,
        similarity=bt.similarity,
        aligned=bt.aligned,
        threshold=bt.threshold,
    )


def _to_multi_query_dataclass(mq: MultiQueryResponse):
    from app.validation.multi_query import MultiQueryReport
    return MultiQueryReport(
        primary_rows=mq.primary_rows,
        alternative_rows=mq.alternative_rows,
        agreement=mq.agreement,
        alternative_sql=mq.alternative_sql,
        error=mq.error,
    )


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    qid = uuid.uuid4().hex[:12]
    schema = _get_schema()
    llm = _get_llm()

    filtered = filter_relevant_tables(schema, req.question)
    log.info("query received", extra={
        "qid": qid, "question": req.question,
        "method": filtered.method, "candidate_tables": filtered.tables,
    })

    try:
        gen = generate_sql(llm, req.question, schema, filtered.tables)
    except Exception as exc:
        log.exception("sql generation failed")
        raise HTTPException(status_code=500, detail=f"SQL generation failed: {exc}")

    blocked = False
    block_reason: GuardrailWarning | None = None
    exec_result: ExecutionResult | None = None
    syntax_ok = True
    try:
        exec_result = run_safely(gen.sql)
    except GuardrailViolation as gv:
        blocked = True
        block_reason = GuardrailWarning(rule=gv.rule, message=gv.message)
        log.warning("guardrail blocked query", extra={"qid": qid, "rule": gv.rule, "sql": gen.sql})
    except SqlSyntaxError as exc:
        syntax_ok = False
        block_reason = GuardrailWarning(rule="SQL_SYNTAX", message=str(exc))
        blocked = True
        log.warning("sql syntax invalid", extra={"qid": qid, "err": str(exc)})

    bt_resp: BackTranslationResponse | None = None
    sanity_findings: list[SanityFindingResponse] = []
    mq_resp: MultiQueryResponse | None = None

    if exec_result and not blocked:
        if req.enable_back_translation:
            try:
                bt = back_translate_and_score(llm, req.question, exec_result.sql, schema, filtered.tables)
                bt_resp = BackTranslationResponse(
                    back_translation=bt.back_translation, similarity=bt.similarity,
                    aligned=bt.aligned, threshold=bt.threshold,
                )
            except Exception as exc:
                log.warning("back-translation failed: %s", exc)

        sanity_report = check_results(exec_result, question=req.question)
        sanity_findings = [
            SanityFindingResponse(code=f.code, message=f.message, severity=f.severity)
            for f in sanity_report.findings
        ]

        if req.enable_multi_query:
            try:
                alt = generate_alternative_sql(llm, req.question, schema, filtered.tables)
                mq = run_multi_query(exec_result, alt.sql)
                mq_resp = MultiQueryResponse(
                    alternative_sql=mq.alternative_sql, agreement=mq.agreement,
                    primary_rows=mq.primary_rows, alternative_rows=mq.alternative_rows,
                    error=mq.error,
                )
            except Exception as exc:
                log.warning("multi-query check failed: %s", exc)
                mq_resp = MultiQueryResponse(
                    alternative_sql=None, agreement=0.0,
                    primary_rows=exec_result.row_count, alternative_rows=0,
                    error=str(exc),
                )
    else:
        sanity_report = check_results(
            ExecutionResult(sql=gen.sql, columns=[], rows=[], row_count=0, execution_ms=0.0),
            question=req.question,
        )

    confidence = compute_confidence(
        syntax_ok=syntax_ok,
        back_translation=(None if bt_resp is None else _to_back_translation_dataclass(bt_resp)),
        sanity=sanity_report,
        multi_query=(None if mq_resp is None else _to_multi_query_dataclass(mq_resp)),
        tables_used=gen.tables_used,
        candidate_tables=filtered.tables,
    )

    response = QueryResponse(
        query_id=qid,
        question=req.question,
        sql=(exec_result.sql if exec_result else gen.sql),
        explanation=gen.explanation,
        is_ambiguous=gen.is_ambiguous,
        interpretations=gen.interpretations,
        columns=(exec_result.columns if exec_result else []),
        rows=(exec_result.rows if exec_result else []),
        row_count=(exec_result.row_count if exec_result else 0),
        execution_ms=(exec_result.execution_ms if exec_result else 0.0),
        explain_plan=(exec_result.explain_plan if exec_result else []),
        blocked=blocked,
        block_reason=block_reason,
        guardrail_warnings=(exec_result.warnings if exec_result else []),
        rules_passed=(exec_result.rules_passed if exec_result else []),
        back_translation=bt_resp,
        sanity_findings=sanity_findings,
        multi_query=mq_resp,
        confidence=ConfidenceResponse(**confidence.__dict__),
    )

    _HISTORY.append({
        "query_id": qid, "question": req.question, "sql": response.sql,
        "confidence": confidence.composite, "blocked": blocked,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return response


@router.get("/schema", response_model=SchemaResponse)
def schema_endpoint() -> SchemaResponse:
    schema = _get_schema()
    tables = []
    for t in schema.tables:
        cols = [
            {
                "name": c.name, "type": c.type, "nullable": c.nullable,
                "is_primary_key": c.is_primary_key, "comment": c.comment,
                "sample_values": c.sample_values,
            }
            for c in t.columns
        ]
        fks = [
            {"column": fk.column, "references_table": fk.references_table,
             "references_column": fk.references_column}
            for fk in t.foreign_keys
        ]
        tables.append(TableSummary(name=t.name, comment=t.comment, columns=cols, foreign_keys=fks))
    return SchemaResponse(tables=tables)


@router.get("/history", response_model=HistoryResponse)
def history_endpoint() -> HistoryResponse:
    return HistoryResponse(items=[HistoryItem(**h) for h in _HISTORY[-50:]])


@router.post("/feedback")
def feedback_endpoint(req: FeedbackRequest) -> dict[str, Any]:
    _FEEDBACK.append({**req.model_dump(), "timestamp": datetime.now(timezone.utc).isoformat()})
    log.info("feedback received", extra={"query_id": req.query_id, "correct": req.correct})
    return {"ok": True, "stored": len(_FEEDBACK)}


@router.get("/health")
def health_endpoint() -> dict[str, str]:
    return {"status": "ok"}
