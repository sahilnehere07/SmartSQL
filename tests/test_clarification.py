"""Focused tests for clarified SQL guidance and grounded entity options."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_DIR))

from _lib import db, llm, prompts
from _lib.models import QueryAnalysis


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.rolled_back = False
        self.closed = False

    def cursor(self, **_kwargs):
        return self.fake_cursor

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("question", "expected_first_name"),
    [
        ("Show me John's orders.", "John"),
        ("Show me John Iyer's orders.", "John Iyer"),
        ("What has John bought?", "John"),
        ("How much has John spent?", "John"),
    ],
)
def test_customer_candidate_lookup_is_read_only_and_uses_actual_rows(
    monkeypatch, question, expected_first_name
):
    rows = [
        {"id": 1, "name": "John Iyer"},
        {"id": 2, "name": "John Singh"},
    ]
    cursor = _Cursor(rows)
    connection = _Connection(cursor)
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    result = db.find_customer_candidates(question)

    assert result == {"first_name": expected_first_name, "customers": rows}
    assert cursor.executed[0][0] == "SET TRANSACTION READ ONLY"
    assert "FROM customers" in cursor.executed[1][0]
    assert "lower(%s)" in cursor.executed[1][0]
    assert cursor.executed[1][1] == (
        expected_first_name, expected_first_name, expected_first_name
    )
    assert connection.rolled_back and connection.closed


def test_non_entity_clarification_does_not_query_customer_candidates(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_connection",
        lambda: pytest.fail("Metric clarification must not look up customer rows"),
    )

    assert db.find_customer_candidates("Who is the best customer?") is None
    assert db.find_customer_candidates("Show me orders from Mumbai.") is None


def test_ambiguous_customer_options_are_replaced_with_real_database_candidates(
    monkeypatch,
):
    candidates = {
        "first_name": "John",
        "customers": [
            {"id": 29, "name": "John Fernandes"},
            {"id": 30, "name": "John Gupta"},
            {"id": 1, "name": "John Iyer"},
            {"id": 3, "name": "John Khan"},
        ],
    }
    captured = {}
    monkeypatch.setattr(llm, "find_customer_candidates", lambda _question: candidates)
    monkeypatch.setattr(llm, "_get_client", lambda: object())

    def fake_generate(_client, _model, system_prompt, user_prompt):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return SimpleNamespace(
            parsed=QueryAnalysis(
                is_ambiguous=True,
                reason="John could identify multiple customers.",
                clarification_question="Which John?",
                options=["John Smith (ID: 101)", "John Doe (ID: 105)"],
                sql_query=None,
            )
        )

    monkeypatch.setattr(llm, "_call_gemini", fake_generate)

    analysis = llm.analyze_question("Show me John's orders.")

    assert analysis.options == [
        "John Fernandes (ID: 29)",
        "John Gupta (ID: 30)",
        "John Iyer (ID: 1)",
        "Other (enter full name, email, or customer ID)",
    ]
    assert "John Smith" not in str(analysis.options)
    assert "John Iyer (customer id: 1)" in captured["system_prompt"]
    assert "never invent customer names" in captured["system_prompt"].casefold()


def test_clarified_sql_prompt_preserves_scope_and_result_grain():
    system_prompt = prompts.build_system_prompt()
    user_prompt = prompts.build_user_prompt(
        "What has John bought?",
        SimpleNamespace(
            original_question="What has John bought?",
            clarification_question="Which John?",
            user_answer="John Iyer",
        ),
    )
    normalized_prompt = " ".join(system_prompt.split()).casefold()

    assert "preserve the original question's entity, filters, date range" in normalized_prompt
    assert "stable primary-key tie-breaker" in normalized_prompt
    assert "return five" in normalized_prompt
    assert "sum(order_items.quantity) grouped by product id and name" in normalized_prompt
    assert "do not filter order status unless requested" in normalized_prompt
    assert "previous calendar month" in normalized_prompt
    assert "limit matching the requested ranking cardinality" in normalized_prompt
    assert "What has John bought?" in user_prompt
    assert "John Iyer" in user_prompt


def test_missing_customer_candidates_never_create_fabricated_names():
    prompt = prompts.build_system_prompt(
        {"first_name": "Nonexistent", "customers": []}
    )

    assert "No database customers match 'Nonexistent'" in prompt
    assert "Do not offer names" in prompt
    assert "full name, email, or customer id" in prompt.casefold()
