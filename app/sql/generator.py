"""High-level SQL generation: prompt → LLM → typed result."""
from __future__ import annotations

from app.llm.client import GeneratedSQL, LLMClient
from app.llm.prompts import (
    ALT_SOLUTION_SYSTEM,
    SYSTEM_PROMPT,
    build_generation_prompt,
)
from app.schema.introspector import SchemaInfo


def generate_sql(
    client: LLMClient,
    question: str,
    schema: SchemaInfo,
    table_subset: list[str] | None = None,
) -> GeneratedSQL:
    user_prompt = build_generation_prompt(question, schema, table_subset)
    return client.generate_sql(system=SYSTEM_PROMPT, user=user_prompt)


def generate_alternative_sql(
    client: LLMClient,
    question: str,
    schema: SchemaInfo,
    table_subset: list[str] | None = None,
) -> GeneratedSQL:
    """Ask the LLM to solve the same question via a structurally different
    approach. Used by the multi-query validator.
    """
    user_prompt = build_generation_prompt(question, schema, table_subset)
    return client.generate_sql(system=ALT_SOLUTION_SYSTEM, user=user_prompt, temperature=0.3)
