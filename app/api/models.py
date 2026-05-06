"""Pydantic models for the public API."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Request models ---
class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    enable_multi_query: bool = Field(
        default=True,
        description="Run an alternative SQL formulation and check agreement.",
    )
    enable_back_translation: bool = Field(
        default=True,
        description="Run the SQL-to-question back-translation alignment check.",
    )


class FeedbackRequest(BaseModel):
    query_id: str
    correct: bool
    note: str | None = None


# --- Response models ---
class GuardrailWarning(BaseModel):
    rule: str
    message: str


class ConfidenceResponse(BaseModel):
    composite:        float
    syntax_validity:  float
    back_translation: float
    sanity_score:     float
    multi_query:      float
    schema_coverage:  float
    notes:            list[str] = Field(default_factory=list)


class BackTranslationResponse(BaseModel):
    back_translation: str
    similarity:       float
    aligned:          bool
    threshold:        float


class SanityFindingResponse(BaseModel):
    code:     str
    message:  str
    severity: Literal["info", "warn", "error"]


class MultiQueryResponse(BaseModel):
    alternative_sql: str | None
    agreement:       float
    primary_rows:    int
    alternative_rows: int
    error:           str | None = None


class QueryResponse(BaseModel):
    query_id:           str
    question:           str
    sql:                str
    explanation:        str
    is_ambiguous:       bool
    interpretations:    list[str] = Field(default_factory=list)

    columns:            list[str] = Field(default_factory=list)
    rows:               list[list[Any]] = Field(default_factory=list)
    row_count:          int = 0
    execution_ms:       float = 0.0
    explain_plan:       list[str] = Field(default_factory=list)

    blocked:            bool = False
    block_reason:       Optional[GuardrailWarning] = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    rules_passed:       list[str] = Field(default_factory=list)

    back_translation:   Optional[BackTranslationResponse] = None
    sanity_findings:    list[SanityFindingResponse] = Field(default_factory=list)
    multi_query:        Optional[MultiQueryResponse] = None
    confidence:         ConfidenceResponse


class HistoryItem(BaseModel):
    query_id:    str
    question:    str
    sql:         str
    confidence:  float
    blocked:     bool
    timestamp:   str


class HistoryResponse(BaseModel):
    items: list[HistoryItem]


class TableSummary(BaseModel):
    name:        str
    comment:     str | None
    columns:     list[dict[str, Any]]
    foreign_keys: list[dict[str, str]]


class SchemaResponse(BaseModel):
    tables: list[TableSummary]
