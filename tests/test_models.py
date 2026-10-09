import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from _lib.models import QueryAnalysis, AskRequest, ClarificationContext


def test_query_analysis_strips_trailing_semicolon():
    qa = QueryAnalysis(
        is_ambiguous=False,
        reason="Clear question.",
        sql_query="SELECT * FROM customers;",
    )
    assert qa.sql_query == "SELECT * FROM customers"


def test_ask_request_without_clarification():
    req = AskRequest.model_validate({"question": "How many customers are there?"})
    assert req.clarification is None


def test_ask_request_with_clarification():
    req = AskRequest.model_validate({
        "question": "Who is the best customer?",
        "clarification": {
            "original_question": "Who is the best customer?",
            "clarification_question": "By spend or by order count?",
            "user_answer": "By spend",
        },
    })
    assert isinstance(req.clarification, ClarificationContext)
    assert req.clarification.user_answer == "By spend"
