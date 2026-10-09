"""
Pydantic models used across the backend.

QueryAnalysis is the exact shape we force Gemini to return via
response_schema. Keeping it narrow and strict is what makes the
"ambiguity detection" feature reliable instead of a vague prompt hack.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class QueryAnalysis(BaseModel):
    """What the model must return for every question it is asked."""

    is_ambiguous: bool = Field(
        description="True if the question has more than one reasonable "
        "interpretation given the schema, OR if it refers to something "
        "that is not uniquely identifiable in the data (e.g. a name that "
        "matches multiple customers)."
    )
    reason: str = Field(
        description="One sentence explaining WHY it is or isn't ambiguous."
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="Required if is_ambiguous is true. A short question "
        "to ask the user to resolve the ambiguity.",
    )
    options: Optional[list[str]] = Field(
        default=None,
        description="Required if is_ambiguous is true. 2-4 short answer "
        "options the user could pick from.",
    )
    sql_query: Optional[str] = Field(
        default=None,
        description="Required if is_ambiguous is false. A single "
        "read-only PostgreSQL SELECT statement that answers the question.",
    )

    @field_validator("sql_query")
    @classmethod
    def sql_required_when_not_ambiguous(cls, v, info):
        # Pydantic v2: cross-field checks are easiest done at the call site
        # (see llm.py) because field order isn't guaranteed here. This
        # validator just strips whitespace/trailing semicolons.
        if v is not None:
            v = v.strip()
            if v.endswith(";"):
                v = v[:-1]
        return v


class ClarificationContext(BaseModel):
    """Sent by the frontend on the second call, after the user answers."""

    original_question: str
    clarification_question: str
    user_answer: str


class AskRequest(BaseModel):
    question: str
    clarification: Optional[ClarificationContext] = None


class AskResponse(BaseModel):
    status: str  # "needs_clarification" | "success" | "error"
    clarification_question: Optional[str] = None
    options: Optional[list[str]] = None
    answer: Optional[str] = None
    sql: Optional[str] = None
    rows: Optional[list[dict]] = None
    row_count: Optional[int] = None
    message: Optional[str] = None
    latency_ms: Optional[int] = None
