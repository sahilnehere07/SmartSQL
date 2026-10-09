"""
INTEGRATION TESTS -- these require:
  - DATABASE_URL pointing at a running Postgres with schema.sql + seed.sql loaded
  - GEMINI_API_KEY set

They make real network calls and will be skipped automatically if those
environment variables aren't set, so `pytest` stays runnable offline for
the unit tests in this folder.

Run with:  pytest tests/test_integration.py -v
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

requires_live_services = pytest.mark.skipif(
    not (os.environ.get("DATABASE_URL") and os.environ.get("GEMINI_API_KEY")),
    reason="DATABASE_URL and GEMINI_API_KEY must be set for integration tests",
)


@requires_live_services
def test_clear_question_returns_sql():
    from _lib.llm import analyze_question
    analysis = analyze_question("How many customers are there?")
    assert analysis.is_ambiguous is False
    assert analysis.sql_query is not None
    assert "customers" in analysis.sql_query.lower()


@requires_live_services
def test_ambiguous_question_asks_clarification():
    from _lib.llm import analyze_question
    analysis = analyze_question("Who is the best customer?")
    assert analysis.is_ambiguous is True
    assert analysis.clarification_question


@requires_live_services
def test_end_to_end_query_execution():
    from _lib.llm import analyze_question
    from _lib.safety import validate_sql_safety
    from _lib.db import run_readonly_query

    analysis = analyze_question("How many products are in the Electronics category?")
    assert analysis.is_ambiguous is False
    safe_sql = validate_sql_safety(analysis.sql_query)
    rows = run_readonly_query(safe_sql)
    assert isinstance(rows, list)
