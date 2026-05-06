"""Tests for schema introspection and prompt rendering.

Uses an in-memory SQLite database to keep these unit tests fast and
DB-independent. The production path uses Postgres.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.schema.introspector import introspect, render_schema_for_prompt


def _make_engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    with eng.begin() as c:
        c.execute(text("CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT)"))
        c.execute(text("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, FOREIGN KEY (customer_id) REFERENCES customers(customer_id))"))
        c.execute(text("INSERT INTO customers (customer_id, name, country) VALUES (1, 'A', 'US'), (2, 'B', 'GB')"))
        c.execute(text("INSERT INTO orders (order_id, customer_id, status) VALUES (1, 1, 'paid'), (2, 2, 'cancelled')"))
    return eng


def test_introspect_returns_tables_and_columns() -> None:
    eng = _make_engine()
    schema = introspect(eng)
    assert "customers" in schema.table_names()
    assert "orders" in schema.table_names()
    cust = schema.get("customers")
    assert cust is not None
    assert any(c.name == "customer_id" and c.is_primary_key for c in cust.columns)


def test_introspect_picks_up_foreign_keys() -> None:
    eng = _make_engine()
    schema = introspect(eng)
    orders = schema.get("orders")
    assert orders is not None
    fks = [(fk.column, fk.references_table, fk.references_column) for fk in orders.foreign_keys]
    assert ("customer_id", "customers", "customer_id") in fks


def test_render_schema_for_prompt_includes_table_and_columns() -> None:
    eng = _make_engine()
    schema = introspect(eng)
    rendered = render_schema_for_prompt(schema)
    assert "TABLE customers" in rendered
    assert "TABLE orders" in rendered
    assert "FOREIGN KEYS" in rendered


def test_render_schema_filters_to_subset() -> None:
    eng = _make_engine()
    schema = introspect(eng)
    rendered = render_schema_for_prompt(schema, ["customers"])
    assert "customers" in rendered
    assert "TABLE orders" not in rendered
