"""Database schema introspection.

We use SQLAlchemy's reflection to extract a structured representation of
the schema, then enrich it with table/column comments and a sample of
distinct values for low-cardinality string columns. This data is fed
directly into the prompt the LLM sees, so it should be:

* Accurate    - pulled live from information_schema.
* Compact     - no payload data, no row counts beyond what is useful.
* Disambiguating - sample values surface enums and category names so
                   the model writes correct WHERE clauses without guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_primary_key: bool
    comment: str | None = None
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKeyInfo:
    column: str
    references_table: str
    references_column: str


@dataclass
class TableInfo:
    name: str
    comment: str | None
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]
    row_count_estimate: int | None = None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class SchemaInfo:
    tables: list[TableInfo]

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def get(self, name: str) -> TableInfo | None:
        for t in self.tables:
            if t.name == name:
                return t
        return None


_LOW_CARDINALITY_THRESHOLD = 12
_SAMPLE_LIMIT = 5
_SAMPLEABLE_TYPE_PREFIXES = ("VARCHAR", "TEXT", "CHAR", "ENUM")


def _maybe_sample_values(engine: Engine, table: str, column: str, sql_type: str) -> list[str]:
    upper = sql_type.upper()
    if not any(upper.startswith(p) for p in _SAMPLEABLE_TYPE_PREFIXES):
        return []
    try:
        with engine.connect() as conn:
            distinct_count = conn.execute(
                text(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"')
            ).scalar() or 0
            if distinct_count == 0:
                return []
            limit = distinct_count if distinct_count <= _LOW_CARDINALITY_THRESHOLD else _SAMPLE_LIMIT
            rows = conn.execute(
                text(
                    f'SELECT DISTINCT "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL ORDER BY "{column}" LIMIT {limit}'
                )
            ).fetchall()
            return [str(r[0]) for r in rows]
    except Exception:
        return []


def _row_count_estimate(engine: Engine, table: str) -> int | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT reltuples::bigint AS estimate FROM pg_class WHERE relname = :t"),
                {"t": table},
            ).first()
            if row and row.estimate is not None:
                return int(row.estimate)
    except Exception:
        pass
    return None


def introspect(
    engine: Engine,
    include_tables: Iterable[str] | None = None,
    schema: str | None = None,
) -> SchemaInfo:
    """Return a SchemaInfo for the given schema.

    `engine` should be the admin engine when running on Postgres - it
    needs visibility on pg_class statistics and column comments.
    `schema` defaults to "public" for Postgres and None for SQLite.
    """
    if schema is None:
        schema = "public" if engine.dialect.name in {"postgresql"} else None

    inspector = inspect(engine)
    table_names = inspector.get_table_names(schema=schema)
    if include_tables is not None:
        keep = set(include_tables)
        table_names = [t for t in table_names if t in keep]

    tables: list[TableInfo] = []
    for tname in table_names:
        try:
            tcomment = (inspector.get_table_comment(tname, schema=schema) or {}).get("text")
        except Exception:
            tcomment = None
        pk_cols = set(inspector.get_pk_constraint(tname, schema=schema).get("constrained_columns", []) or [])
        cols: list[ColumnInfo] = []
        for col in inspector.get_columns(tname, schema=schema):
            sql_type = str(col["type"])
            cols.append(
                ColumnInfo(
                    name=col["name"],
                    type=sql_type,
                    nullable=bool(col.get("nullable", True)),
                    is_primary_key=col["name"] in pk_cols,
                    comment=col.get("comment"),
                    sample_values=_maybe_sample_values(engine, tname, col["name"], sql_type),
                )
            )

        fks: list[ForeignKeyInfo] = []
        for fk in inspector.get_foreign_keys(tname, schema=schema):
            local_cols = fk.get("constrained_columns", []) or []
            remote_cols = fk.get("referred_columns", []) or []
            remote_tbl = fk.get("referred_table")
            for local, remote in zip(local_cols, remote_cols):
                if remote_tbl:
                    fks.append(ForeignKeyInfo(local, remote_tbl, remote))

        tables.append(
            TableInfo(
                name=tname,
                comment=tcomment,
                columns=cols,
                foreign_keys=fks,
                row_count_estimate=_row_count_estimate(engine, tname),
            )
        )
    return SchemaInfo(tables=tables)


def render_schema_for_prompt(schema: SchemaInfo, tables: Iterable[str] | None = None) -> str:
    """Render a SchemaInfo as a compact, human/LLM readable string."""
    chosen = list(schema.tables) if tables is None else [t for t in schema.tables if t.name in set(tables)]
    blocks: list[str] = []
    for t in chosen:
        lines: list[str] = []
        header = f"TABLE {t.name}"
        if t.comment:
            header += f" -- {t.comment}"
        lines.append(header)
        for c in t.columns:
            parts = [f"  {c.name} {c.type}"]
            if c.is_primary_key:
                parts.append("PK")
            if not c.nullable:
                parts.append("NOT NULL")
            line = " ".join(parts)
            extras: list[str] = []
            if c.comment:
                extras.append(c.comment)
            if c.sample_values:
                preview = ", ".join(c.sample_values[:8])
                extras.append(f"sample: {preview}")
            if extras:
                line += "  -- " + "; ".join(extras)
            lines.append(line)
        if t.foreign_keys:
            lines.append("  FOREIGN KEYS:")
            for fk in t.foreign_keys:
                lines.append(f"    {fk.column} -> {fk.references_table}.{fk.references_column}")
        if t.row_count_estimate is not None and t.row_count_estimate >= 0:
            lines.append(f"  approx_rows: {t.row_count_estimate}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
