"""Thin Anthropic client wrapper with structured-output helpers.

We use Anthropic's tool-use feature to enforce a JSON schema on the
LLM output — that way the rest of the pipeline can rely on getting
back a typed object instead of having to parse free text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from app.config import get_settings


class GeneratedSQL(BaseModel):
    """Structured output we expect from the LLM for SQL generation."""
    sql: str = Field(description="The SQL query. SELECT or WITH only. No semicolons.")
    explanation: str = Field(description="One-paragraph natural-language explanation of what the SQL does.")
    confidence: float = Field(ge=0.0, le=1.0, description="Self-assessed confidence 0.0-1.0.")
    tables_used: list[str] = Field(default_factory=list, description="Tables touched by the query.")
    columns_used: list[str] = Field(default_factory=list, description="Columns referenced (table.column form).")
    is_ambiguous: bool = Field(default=False, description="True if the question has multiple plausible interpretations.")
    interpretations: list[str] = Field(
        default_factory=list,
        description="If ambiguous, the candidate interpretations the LLM considered.",
    )


# JSON schema for the tool-use call. Mirrors GeneratedSQL.
GENERATE_SQL_TOOL_SCHEMA: dict[str, Any] = {
    "name": "submit_sql_answer",
    "description": "Submit a SQL answer to the user's natural-language question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sql":             {"type": "string"},
            "explanation":     {"type": "string"},
            "confidence":      {"type": "number", "minimum": 0, "maximum": 1},
            "tables_used":     {"type": "array", "items": {"type": "string"}},
            "columns_used":    {"type": "array", "items": {"type": "string"}},
            "is_ambiguous":    {"type": "boolean"},
            "interpretations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sql", "explanation", "confidence", "tables_used"],
    },
}


@dataclass
class LLMClient:
    api_key: str
    model:   str
    _client: anthropic.Anthropic | None = None

    def __post_init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=self.api_key)

    @classmethod
    def from_settings(cls) -> "LLMClient":
        s = get_settings()
        if not s.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env or environment."
            )
        return cls(api_key=s.anthropic_api_key, model=s.anthropic_model)

    # ------------------------------------------------------------------ #
    # Structured generation                                              #
    # ------------------------------------------------------------------ #
    def generate_sql(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> GeneratedSQL:
        """Call the LLM and force structured output via tool use."""
        assert self._client is not None
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            tools=[GENERATE_SQL_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "submit_sql_answer"},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_sql_answer":
                payload = block.input
                if isinstance(payload, str):
                    payload = json.loads(payload)
                return GeneratedSQL(**payload)
        raise RuntimeError("LLM did not return a tool_use submit_sql_answer block.")

    # ------------------------------------------------------------------ #
    # Free-text generation (for back-translation)                        #
    # ------------------------------------------------------------------ #
    def generate_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 400,
        temperature: float = 0.0,
    ) -> str:
        assert self._client is not None
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return (block.text or "").strip()
        return ""
