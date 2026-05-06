"""Streamlit UI for the Text-to-SQL service.

Run locally:
    streamlit run frontend/streamlit_app.py
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
TIMEOUT  = 90


# --------------------------------------------------------------------- #
# Page config                                                           #
# --------------------------------------------------------------------- #
st.set_page_config(
    page_title="Text-to-SQL with Guardrails",
    page_icon="🛡",
    layout="wide",
)

st.title("🛡 Text-to-SQL with Guardrails")
st.caption(
    "Plain-English questions → safe SQL → executed read-only against PostgreSQL → "
    "validated for hallucinations → returned with a confidence breakdown."
)


# --------------------------------------------------------------------- #
# Session state                                                         #
# --------------------------------------------------------------------- #
if "history" not in st.session_state:
    st.session_state.history = []
if "schema" not in st.session_state:
    st.session_state.schema = None


def _fetch_schema() -> dict[str, Any] | None:
    try:
        r = requests.get(f"{API_BASE}/v1/schema", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Could not fetch schema from {API_BASE}: {exc}")
        return None


def _post_query(question: str, multi: bool, bt: bool) -> dict[str, Any] | None:
    try:
        r = requests.post(
            f"{API_BASE}/v1/query",
            json={
                "question": question,
                "enable_multi_query": multi,
                "enable_back_translation": bt,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        return None


def _post_feedback(query_id: str, correct: bool, note: str = "") -> None:
    try:
        requests.post(
            f"{API_BASE}/v1/feedback",
            json={"query_id": query_id, "correct": correct, "note": note},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        st.warning(f"Feedback not stored: {exc}")


# --------------------------------------------------------------------- #
# Sidebar                                                               #
# --------------------------------------------------------------------- #
with st.sidebar:
    st.header("Settings")
    enable_mq = st.toggle("Multi-query agreement", value=True)
    enable_bt = st.toggle("Back-translation check",  value=True)

    st.markdown("---")
    st.subheader("Schema")
    if st.button("Refresh schema"):
        st.session_state.schema = _fetch_schema()
    if st.session_state.schema is None:
        st.session_state.schema = _fetch_schema()
    if st.session_state.schema:
        for table in st.session_state.schema.get("tables", []):
            with st.expander(f"📦 {table['name']}"):
                if table.get("comment"):
                    st.caption(table["comment"])
                cols_df = pd.DataFrame([
                    {
                        "column": c["name"],
                        "type": c["type"],
                        "PK": "✓" if c["is_primary_key"] else "",
                        "null": "" if c["nullable"] else "NOT NULL",
                        "comment": c.get("comment") or "",
                    }
                    for c in table.get("columns", [])
                ])
                st.dataframe(cols_df, hide_index=True, use_container_width=True)


# --------------------------------------------------------------------- #
# Question input                                                        #
# --------------------------------------------------------------------- #
EXAMPLES = [
    "How many customers do we have in each country?",
    "What was the total net revenue last month?",
    "Top 5 best-selling products by units sold in 2025.",
    "Which categories have the highest gross margin?",
    "Drop the customers table.",  # demonstrates the guardrail
]

cols = st.columns([3, 1])
with cols[0]:
    question = st.text_input(
        "Ask a question about the database",
        placeholder="e.g. Top 5 best-selling products this year",
    )
with cols[1]:
    submit = st.button("Run", use_container_width=True, type="primary")

example = st.selectbox("…or try one of these", [""] + EXAMPLES, index=0)
if example and not question:
    question = example


# --------------------------------------------------------------------- #
# Render a query response                                               #
# --------------------------------------------------------------------- #
def _confidence_badge(score: float) -> str:
    if score >= 0.85:
        return f"🟢 {score:.0%}"
    if score >= 0.65:
        return f"🟡 {score:.0%}"
    return f"🔴 {score:.0%}"


def render_response(resp: dict[str, Any]) -> None:
    qid = resp.get("query_id", "?")
    confidence = resp["confidence"]

    top = st.columns([2, 1, 1])
    top[0].markdown(f"**Question:** {resp['question']}")
    top[1].markdown(f"**Confidence:** {_confidence_badge(confidence['composite'])}")
    top[2].markdown(f"**Rows returned:** {resp['row_count']}")

    if resp.get("blocked"):
        br = resp.get("block_reason") or {}
        st.error(
            f"🛑 Query blocked by guardrail [{br.get('rule', 'UNKNOWN')}]: "
            f"{br.get('message', '')}"
        )

    if resp.get("is_ambiguous"):
        st.warning("This question is ambiguous. Possible interpretations:")
        for i, interp in enumerate(resp.get("interpretations", []), start=1):
            st.markdown(f"- **Interpretation {i}:** {interp}")

    # SQL block.
    st.subheader("Generated SQL")
    st.code(resp["sql"], language="sql")
    st.caption(resp.get("explanation", ""))

    if resp.get("guardrail_warnings"):
        for w in resp["guardrail_warnings"]:
            st.info(f"ℹ Guardrail note: {w}")

    # Results table.
    if resp.get("rows"):
        st.subheader("Results")
        df = pd.DataFrame(resp["rows"], columns=resp["columns"])
        st.dataframe(df, use_container_width=True)
        st.caption(f"Executed in {resp.get('execution_ms', 0):.1f} ms")

    # Confidence breakdown.
    with st.expander("🔍 Confidence breakdown", expanded=not resp.get("blocked")):
        cols_c = st.columns(5)
        cols_c[0].metric("Syntax",        f"{confidence['syntax_validity']:.0%}")
        cols_c[1].metric("Back-translate", f"{confidence['back_translation']:.0%}")
        cols_c[2].metric("Sanity",        f"{confidence['sanity_score']:.0%}")
        cols_c[3].metric("Multi-query",   f"{confidence['multi_query']:.0%}")
        cols_c[4].metric("Coverage",      f"{confidence['schema_coverage']:.0%}")
        for note in confidence.get("notes", []):
            st.markdown(f"- {note}")

    # Hallucination detail.
    if resp.get("back_translation"):
        bt = resp["back_translation"]
        with st.expander("🔁 Back-translation"):
            st.markdown(f"**Back-translated question:** {bt['back_translation']}")
            st.markdown(
                f"**Similarity:** {bt['similarity']:.2f}  "
                f"**Threshold:** {bt['threshold']:.2f}  "
                f"**Aligned:** {'✅' if bt['aligned'] else '⚠'}"
            )

    if resp.get("multi_query"):
        mq = resp["multi_query"]
        with st.expander("🔀 Multi-query agreement"):
            st.markdown(f"**Agreement:** {mq['agreement']:.2f}")
            st.markdown(
                f"Primary rows: {mq['primary_rows']} | Alternative rows: {mq['alternative_rows']}"
            )
            if mq.get("alternative_sql"):
                st.code(mq["alternative_sql"], language="sql")
            if mq.get("error"):
                st.warning(f"Alternative SQL error: {mq['error']}")

    if resp.get("sanity_findings"):
        with st.expander("🩺 Sanity findings"):
            for f in resp["sanity_findings"]:
                icon = {"info": "ℹ", "warn": "⚠", "error": "❌"}.get(f["severity"], "•")
                st.markdown(f"- {icon} **{f['code']}** — {f['message']}")

    if resp.get("explain_plan"):
        with st.expander("🗒 EXPLAIN plan"):
            st.code("\n".join(resp["explain_plan"]), language="text")

    # Feedback row.
    fb = st.columns([1, 1, 6])
    if fb[0].button("👍 Correct", key=f"good-{qid}"):
        _post_feedback(qid, True)
        st.toast("Marked correct.")
    if fb[1].button("👎 Wrong", key=f"bad-{qid}"):
        _post_feedback(qid, False)
        st.toast("Marked wrong; logged for retraining set.")


# --------------------------------------------------------------------- #
# Submit                                                                #
# --------------------------------------------------------------------- #
if submit and question.strip():
    with st.spinner("Generating, executing, and validating…"):
        resp = _post_query(question.strip(), enable_mq, enable_bt)
    if resp:
        st.session_state.history.insert(0, resp)
        render_response(resp)


# --------------------------------------------------------------------- #
# History panel                                                         #
# --------------------------------------------------------------------- #
if st.session_state.history and not submit:
    st.subheader("History")
    for past in st.session_state.history[:5]:
        with st.expander(
            f"{_confidence_badge(past['confidence']['composite'])}  {past['question']}",
            expanded=False,
        ):
            render_response(past)
