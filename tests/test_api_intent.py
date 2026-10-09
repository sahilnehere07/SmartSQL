"""Focused API tests for natural-language destructive-request blocking."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_API_DIR = Path(
    os.environ.get(
        "SMARTSQL_PROJECT_API_DIR",
        Path(__file__).resolve().parents[1] / "api",
    )
)
sys.path.insert(0, str(PROJECT_API_DIR))

staged_module = os.environ.get("SMARTSQL_API_MODULE_PATH")
if staged_module:
    spec = importlib.util.spec_from_file_location("smartsql_api_under_test", staged_module)
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
else:
    import index as api


@pytest.mark.parametrize(
    "question",
    [
        "Delete all customers from Mumbai.",
        "Delete all customers.",
        "Update customer city.",
        "Drop the customers table.",
        "Truncate the orders table.",
        "Remove all orders.",
    ],
)
def test_destructive_requests_are_refused_before_analysis_or_execution(
    monkeypatch, question
):
    def unexpected_call(*args, **kwargs):
        pytest.fail("Destructive requests must stop before SQL generation/execution")

    monkeypatch.setattr(api, "analyze_question", unexpected_call)
    monkeypatch.setattr(api, "run_readonly_query", unexpected_call)

    response = api.app.test_client().post("/api/ask", json={"question": question})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "destructive" in payload["message"].casefold()
    assert "not supported" in payload["message"].casefold()
    assert "sql" not in payload


def test_destructive_clarification_answer_is_refused(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("Destructive clarification must stop before SQL generation/execution")

    monkeypatch.setattr(api, "analyze_question", unexpected_call)
    monkeypatch.setattr(api, "run_readonly_query", unexpected_call)
    response = api.app.test_client().post(
        "/api/ask",
        json={
            "question": "What should I do with the customers?",
            "clarification": {
                "original_question": "What should I do with the customers?",
                "clarification_question": "What operation do you mean?",
                "user_answer": "Delete all customers.",
            },
        },
    )
    assert response.status_code == 400
    assert response.get_json()["status"] == "error"
    assert "destructive" in response.get_json()["message"].casefold()


@pytest.mark.parametrize(
    "question",
    [
        "How many customers are there?",
        "How many customers are from Mumbai?",
    ],
)
def test_clear_read_questions_still_execute(monkeypatch, question):
    executed = []
    monkeypatch.setattr(
        api,
        "analyze_question",
        lambda _question, _clarification: SimpleNamespace(
            is_ambiguous=False, sql_query="SELECT COUNT(*) AS value FROM customers"
        ),
    )
    monkeypatch.setattr(
        api, "run_readonly_query", lambda sql: executed.append(sql) or [{"value": 35}]
    )
    monkeypatch.setattr(api, "explain_results", lambda *_args: "There are 35 customers.")

    response = api.app.test_client().post("/api/ask", json={"question": question})

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"
    assert len(executed) == 1


def test_ambiguous_read_question_still_reaches_clarification(monkeypatch):
    analysis_calls = []
    monkeypatch.setattr(
        api,
        "analyze_question",
        lambda question, _clarification: analysis_calls.append(question)
        or SimpleNamespace(
            is_ambiguous=True,
            clarification_question="Do you mean highest spend or most orders?",
            options=["Highest spend", "Most orders"],
        ),
    )
    monkeypatch.setattr(
        api,
        "run_readonly_query",
        lambda *_args: pytest.fail("Ambiguous question must not execute SQL"),
    )

    response = api.app.test_client().post(
        "/api/ask", json={"question": "Who is the best customer?"}
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "needs_clarification"
    assert analysis_calls == ["Who is the best customer?"]
