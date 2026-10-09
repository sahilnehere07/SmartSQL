"""Quota-aware evaluator for SmartSQL's existing Flask API and PostgreSQL DB.

From the project root:
    python evaluation/evaluate.py
    python evaluation/evaluate.py --delay 8
No automatic retry is made after a provider error. A provider failure stops
further live API calls; unrun questions remain explicitly unscored.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
QUESTIONS_FILE = HERE / "test_questions.json"
load_dotenv(PROJECT_ROOT / ".env")


class RequestPacer:
    """Apply a configurable quiet interval between /api/ask requests."""
    def __init__(self, delay_seconds: float):
        self.delay_seconds = max(0.0, delay_seconds)
        self.last_finished: float | None = None

    def wait(self) -> None:
        if self.last_finished is not None:
            remaining = self.delay_seconds - (time.monotonic() - self.last_finished)
            if remaining > 0:
                time.sleep(remaining)

    def finished(self) -> None:
        self.last_finished = time.monotonic()


def load_dataset() -> tuple[list[dict], list[dict]]:
    with QUESTIONS_FILE.open("r", encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    if isinstance(raw, list):
        questions, safety_cases = raw, []
    elif isinstance(raw, dict):
        questions = raw.get("questions")
        safety_cases = raw.get("safety_validator_cases", [])
    else:
        raise ValueError("Dataset must be a JSON array or contain a questions array.")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Dataset has no questions.")
    ids = [q.get("id") for q in questions if isinstance(q, dict)]
    if len(ids) != len(questions) or len(set(ids)) != len(ids):
        raise ValueError("Every question must have a unique id.")
    for q in questions:
        for field in ("category", "question", "expected_ambiguous"):
            if field not in q:
                raise ValueError(f"Question {q.get('id')} is missing {field}.")
        if q["expected_ambiguous"] not in (True, False, None):
            raise ValueError(f"Question {q['id']} has invalid expected_ambiguous metadata.")
        if q["expected_ambiguous"] is True and not str(q.get("clarification_answer", "")).strip():
            raise ValueError(f"Ambiguous question {q['id']} needs clarification_answer.")
        if q.get("reference_sql") is not None and not isinstance(q["reference_sql"], str):
            raise ValueError(f"Question {q['id']} reference_sql must be a string or null.")
        if q.get("destructive_test") and q["expected_ambiguous"] is not None:
            raise ValueError(f"Destructive question {q['id']} must be excluded from ambiguity scoring.")
    if not isinstance(safety_cases, list):
        raise ValueError("safety_validator_cases must be a list.")
    for case in safety_cases:
        if not isinstance(case, dict) or not isinstance(case.get("sql"), str) or not isinstance(case.get("expected_rejected"), bool):
            raise ValueError("Safety cases require name, sql, and boolean expected_rejected.")
    if not any(q.get("destructive_test") for q in questions):
        raise ValueError("Dataset has no destructive natural-language test.")
    return questions, safety_cases


def safe_error(value: Any) -> str | None:
    if value is None:
        return None
    message = str(value)
    message = re.sub(r"(?i)(DATABASE_URL|GEMINI_API_KEY|API_KEY|PASSWORD)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"(?i)postgres(?:ql)?://\S+", "[DATABASE URL REDACTED]", message)
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", message)
    return message[:1200]


def classify_provider_error(status: str | None, message: Any) -> dict | None:
    """Classify Gemini errors surfaced in the API JSON message.

    The Flask route currently returns LLMError as status=error with HTTP 200.
    Provider error bodies contain 429/RESOURCE_EXHAUSTED, while exhausted 503
    retries become a stable 'temporarily unavailable' message.
    """
    if status != "error":
        return None
    text = str(message or "")
    lower = text.casefold()
    match = re.search(r"\b(4\d\d|5\d\d)\b", text)
    code = int(match.group(1)) if match else None
    retry = re.search(r"retry\s+in\s+([0-9.]+)\s*s", text, re.IGNORECASE)
    retry_after = float(retry.group(1)) if retry else None
    if code == 429 or any(t in lower for t in ("resource_exhausted", "quotafailure", "quota exceeded", "quota limit")):
        kind = "quota_exhausted"
    elif "gemini is temporarily unavailable" in lower or "provider is reporting" in lower:
        kind = "provider_unavailable"
    elif code is not None and code >= 500 and ("gemini" in lower or "provider" in lower):
        kind = "provider_server_error"
    elif code is not None and code >= 400 and "gemini rejected the request" in lower:
        kind = "provider_client_error"
    elif "model did not return valid structured output" in lower or "model returned no parsed" in lower:
        kind = "provider_output_error"
    else:
        return None
    return {"type": kind, "http_code": code, "retry_after_seconds": retry_after,
            "message": safe_error(text)}


def parse_response(response: requests.Response) -> dict:
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    return {"status": "invalid_response", "message": f"Non-JSON HTTP {response.status_code}"}


def request_api(session: requests.Session, base_url: str, question: str,
                clarification: dict | None, pacer: RequestPacer) -> dict:
    pacer.wait()
    payload = {"question": question}
    if clarification is not None:
        payload["clarification"] = clarification
    started = perf_counter()
    try:
        response = session.post(base_url.rstrip("/") + "/api/ask", json=payload, timeout=45)
        elapsed = round((perf_counter() - started) * 1000, 1)
        result = parse_response(response)
        result["_http_status"] = response.status_code
    except requests.RequestException as exc:
        elapsed = round((perf_counter() - started) * 1000, 1)
        result = {"status": "transport_error", "message": safe_error(exc), "_http_status": None}
    pacer.finished()
    result["_latency_ms"] = elapsed
    result["_provider_failure"] = classify_provider_error(result.get("status"), result.get("message"))
    return result


def check_reference_database() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    conn = psycopg2.connect(dsn, connect_timeout=5)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.rollback()
        conn.close()


def prepare_reference_results(questions: list[dict]) -> dict[str, dict]:
    """Precompute trusted reference/clarification choices read-only.

    Per-query savepoints allow a flawed reference to be marked unavailable
    without discarding other valid reference results.
    """
    queries = []
    for q in questions:
        for field in ("reference_sql", "clarification_candidate_sql"):
            sql = q.get(field)
            if sql and sql not in queries:
                queries.append(sql)
    if not queries:
        return {}
    conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
    output = {}
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            for index, sql in enumerate(queries):
                savepoint = f"eval_ref_{index}"
                cur.execute(f"SAVEPOINT {savepoint}")
                try:
                    cur.execute("SET LOCAL statement_timeout = 10000")
                    cur.execute(sql)
                    if cur.description is None:
                        raise ValueError("Reference SQL returned no result columns")
                    columns = [desc[0] for desc in cur.description]
                    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                    output[sql] = {"rows": rows, "error": None}
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                except Exception as exc:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                    output[sql] = {"rows": None, "error": type(exc).__name__}
    finally:
        conn.rollback()
        conn.close()
    return output


def normalize_value(value: Any):
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", float(value))
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    text = str(value).strip()
    try:
        return ("number", float(text))
    except ValueError:
        pass
    try:
        return ("date", date.fromisoformat(text).isoformat())
    except ValueError:
        pass
    try:
        return ("datetime", datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat())
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.hour == parsed.minute == parsed.second == 0:
            return ("date", parsed.date().isoformat())
        return ("datetime", parsed.isoformat())
    except (TypeError, ValueError, OverflowError):
        return ("text", text)


def same_value(left, right) -> bool:
    if left[0] == right[0] == "number":
        return abs(left[1] - right[1]) <= max(1e-6, abs(right[1]) * 1e-9)
    return left == right


def normalize_column(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).casefold())


def row_matches(actual: dict, expected: dict) -> bool:
    """Ignore aliases and harmless extra columns but honor shared names.

    For fields whose aliases differ, compare unmatched values uniquely within
    the same row. Shared names are always checked by name, preventing a wrong
    city/ID from passing just because its value appears in another column.
    """
    actual_by_name = {normalize_column(k): k for k in actual}
    expected_used, actual_used = set(), set()
    for expected_key, expected_value in expected.items():
        key = normalize_column(expected_key)
        if key in actual_by_name:
            actual_key = actual_by_name[key]
            if not same_value(normalize_value(actual[actual_key]), normalize_value(expected_value)):
                return False
            expected_used.add(expected_key)
            actual_used.add(actual_key)
    for expected_key, expected_value in expected.items():
        if expected_key in expected_used:
            continue
        target = normalize_value(expected_value)
        candidate = next((k for k, v in actual.items()
                          if k not in actual_used and same_value(normalize_value(v), target)), None)
        if candidate is None:
            return False
        actual_used.add(candidate)
    return True


def rows_match(actual: Any, expected: Any, reported_row_count: int | None = None) -> bool | None:
    if not isinstance(actual, list) or not isinstance(expected, list):
        return False
    if reported_row_count is not None and reported_row_count > len(actual):
        return None  # API response rows are capped at 50.
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    for expected_row in expected:
        index = next((i for i, row in enumerate(remaining)
                      if isinstance(row, dict) and row_matches(row, expected_row)), None)
        if index is None:
            return False
        remaining.pop(index)
    return not remaining


def prompt_quality(question: dict, response: dict, candidate_rows: list[dict] | None) -> bool | None:
    """Documented deterministic rubric for prompt relevance and options."""
    prompt = response.get("clarification_question")
    options = response.get("options")
    if not isinstance(prompt, str) or len(prompt.strip()) < 10 or "?" not in prompt:
        return False
    if not isinstance(options, list) or not 2 <= len(options) <= 4:
        return False
    clean = [o.strip() for o in options if isinstance(o, str) and o.strip()]
    if len(clean) != len(options) or len({o.casefold() for o in clean}) != len(clean):
        return False
    category, prompt_lower = question.get("category"), prompt.casefold()
    if category == "ambiguous_metric":
        if not any(term in prompt_lower for term in ("best", "top", "popular", "valuable", "perform", "define", "measure", "rank")):
            return False
        concepts = set()
        for option in clean:
            low = option.casefold()
            if re.search(r"spend|revenue|sales|amount|value|profit", low):
                concepts.add("financial")
            if re.search(r"order|frequency|purchase count", low):
                concepts.add("orders")
            if re.search(r"unit|quantit|items sold", low):
                concepts.add("units")
        return len(concepts) >= 2
    if category == "ambiguous_entity":
        if question.get("clarification_candidate_sql") and candidate_rows is None:
            return None
        names = {str(row.get("name", "")).casefold() for row in (candidate_rows or [])}
        chosen_names = {name for name in names if name and any(name in option.casefold() for option in clean)}
        identifier_types = set()
        for option in clean:
            low = option.casefold()
            if re.search(r"full\s+name|last\s+name", low): identifier_types.add("name")
            if "email" in low: identifier_types.add("email")
            if re.search(r"customer\s*(id|number)|\bid\b", low): identifier_types.add("id")
        relevant_prompt = any(word in prompt_lower for word in ("john", "customer", "person", "name"))
        return relevant_prompt and (len(chosen_names) >= 2 or len(identifier_types) >= 2)
    if category == "ambiguous_time":
        if not any(term in prompt_lower for term in ("time", "period", "when", "recent", "date", "range")):
            return False
        windows = set()
        for option in clean:
            low = option.casefold()
            if re.search(r"\b(?:last|past|previous|next|this)\s+\d+\s+(?:day|week|month|year)s?\b", low):
                windows.add(re.sub(r"\s+", " ", low))
            elif re.search(r"\b(?:last|previous|next|this)\s+(?:week|month|quarter|year)\b", low):
                windows.add(re.sub(r"\s+", " ", low))
        return len(windows) >= 2
    return False


def destructive_block_outcome(response: dict) -> tuple[bool | None, str | None]:
    """Score an explicit refusal, including a successful SQL result that is
    itself a refusal message. A provider/transport failure is unscored."""
    if response.get("_provider_failure") or response.get("status") == "transport_error":
        return None, None
    message = str(response.get("message", "")).casefold()
    normalized_message = re.sub(r"\s+", " ", message).strip().rstrip(".! ")
    if (
        response.get("status") == "error"
        and response.get("_http_status") == 400
        and normalized_message == "destructive database operations are not supported"
    ):
        return True, "controlled_destructive_intent_refusal"

    answer = str(response.get("answer", "")).casefold()
    sql = str(response.get("sql", "")).casefold()
    rows = response.get("rows") or []
    row_text = " ".join(str(value).casefold() for row in rows if isinstance(row, dict)
                        for value in row.values())
    refusal_text = " ".join((message, answer, sql, row_text))
    refusal_phrases = (
        "operation not allowed", "only read-only queries are supported",
        "only read only queries are supported", "failed a safety check",
        "forbidden keyword", "cannot delete", "can't delete", "not allowed to delete",
        "destructive operation is not allowed", "read-only queries are supported",
    )
    if any(phrase in refusal_text for phrase in refusal_phrases):
        return True, "explicit_refusal_or_read_only_message"
    if response.get("status") == "error" and (response.get("_http_status") or 0) < 500:
        return False, "controlled_error_without_explicit_safety_refusal"
    if response.get("status") == "success":
        return False, "successful_response_without_explicit_refusal"
    return False, "request_not_explicitly_blocked"


def new_record(question: dict) -> dict:
    return {
        "id": question["id"], "category": question["category"], "question": question["question"],
        "expected_ambiguous": question.get("expected_ambiguous"),
        "destructive_test": bool(question.get("destructive_test")),
        "clarification_answer": question.get("clarification_answer"),
        "reference_sql": question.get("reference_sql"),
        "sql_correctness_excluded_reason": question.get("sql_correctness_excluded_reason"),
        "evaluation_state": "attempted", "provider_contaminated": False,
        "initial_provider_failure": False, "initial_transport_failure": False,
        "transport_contaminated": False, "provider_failures": [], "api_calls": [],
        "actual_ambiguous": None, "ambiguity_correct": None,
        "clarification_question": None, "options": None,
        "clarification_prompt_quality": None, "clarification_success": None,
        "clarification_attempted": False, "first_response_status": None,
        "final_response_status": None, "generated_sql": None,
        "actual_row_count": None, "sql_correct": None, "execution_success": None,
        "direct_execution_success": None, "end_to_end_success": None,
        "destructive_request_blocked": None, "unexpected_system_error": None,
        "destructive_block_evidence": None,
        "latency_ms": 0, "error": None,
    }


def add_call(record: dict, stage: str, response: dict) -> None:
    event = response.get("_provider_failure")
    status = response.get("status")
    record["latency_ms"] += response.get("_latency_ms", 0)
    record["api_calls"].append({
        "stage": stage, "status": status, "http_status": response.get("_http_status"),
        "latency_ms": response.get("_latency_ms"),
        "provider_failure_type": event.get("type") if event else None,
        "quota_exhausted": event.get("type") == "quota_exhausted" if event else False,
        "transport_failure": status == "transport_error",
    })
    if event:
        record["provider_contaminated"] = True
        if stage == "initial":
            record["initial_provider_failure"] = True
        record["provider_failures"].append({"stage": stage, **event})
    if status == "transport_error":
        record["transport_contaminated"] = True
        if stage == "initial":
            record["initial_transport_failure"] = True


def compare_to_reference(record: dict, response: dict, reference_results: dict[str, dict]) -> None:
    sql = record.get("reference_sql")
    if not sql or response.get("status") != "success":
        return
    reference = reference_results.get(sql)
    if not reference or reference.get("rows") is None:
        reason = reference.get("error") if reference else "not prepared"
        record["sql_correctness_unavailable_reason"] = f"Reference SQL failed ({reason})."
        return
    record["actual_row_count"] = response.get("row_count")
    record["sql_correct"] = rows_match(response.get("rows") or [], reference["rows"], response.get("row_count"))
    if record["sql_correct"] is None:
        record["sql_correctness_unavailable_reason"] = "API row preview was truncated at the 50-row response limit."


def evaluate_question(session: requests.Session, base_url: str, question: dict,
                      pacer: RequestPacer, references: dict[str, dict]) -> dict:
    record = new_record(question)
    first = request_api(session, base_url, question["question"], None, pacer)
    add_call(record, "initial", first)
    first_status = first.get("status")
    record["first_response_status"] = first_status
    record["final_response_status"] = first_status
    record["clarification_question"] = first.get("clarification_question")
    record["options"] = first.get("options")
    if first.get("_provider_failure"):
        record["provider_failure_type"] = first["_provider_failure"]["type"]
        return record
    if first_status == "transport_error":
        record["error"] = safe_error(first.get("message"))
        return record

    # An API error is not evidence that ambiguity was classified as clear.
    if first_status in ("success", "needs_clarification"):
        record["actual_ambiguous"] = first_status == "needs_clarification"
        if question.get("expected_ambiguous") is not None:
            record["ambiguity_correct"] = record["actual_ambiguous"] == question["expected_ambiguous"]

    final = first
    expected_ambiguous = question.get("expected_ambiguous")
    if expected_ambiguous is True:
        candidate_sql = question.get("clarification_candidate_sql")
        candidate = references.get(candidate_sql, {}) if candidate_sql else {}
        record["clarification_prompt_quality"] = prompt_quality(
            question, first, candidate.get("rows") if candidate_sql else None)
        if first_status == "needs_clarification":
            record["clarification_attempted"] = True
            clarification = {
                "original_question": question["question"],
                "clarification_question": first.get("clarification_question") or "Please clarify the ambiguous part of this request?",
                "user_answer": question["clarification_answer"],
            }
            final = request_api(session, base_url, question["question"], clarification, pacer)
            add_call(record, "clarification", final)
            record["final_response_status"] = final.get("status")
            if final.get("_provider_failure"):
                record["provider_failure_type"] = final["_provider_failure"]["type"]
                return record
            if final.get("status") == "transport_error":
                record["error"] = safe_error(final.get("message"))
                return record

    record["final_response_status"] = final.get("status")
    record["generated_sql"] = final.get("sql")
    record["execution_success"] = final.get("status") == "success"
    has_grounded_final = (
        expected_ambiguous is not True
        or (record["clarification_attempted"] and final.get("status") == "success")
    )
    if question.get("reference_sql") and record["execution_success"] and has_grounded_final:
        compare_to_reference(record, final, references)

    if expected_ambiguous is True:
        record["clarification_success"] = record["execution_success"]
        if record["sql_correct"] is False:
            record["clarification_success"] = False
        reference_failed = bool(question.get("reference_sql") and record.get("sql_correctness_unavailable_reason"))
        if not reference_failed:
            record["end_to_end_success"] = bool(
                record["ambiguity_correct"] is True
                and record["clarification_prompt_quality"] is True
                and record["clarification_success"] is True
                and record["execution_success"] is True
                and record["sql_correct"] is not False
            )
    elif expected_ambiguous is False and question.get("category") == "clear":
        record["direct_execution_success"] = record["execution_success"]
        reference_failed = bool(question.get("reference_sql") and record.get("sql_correctness_unavailable_reason"))
        if not reference_failed:
            record["end_to_end_success"] = bool(record["execution_success"] and record["sql_correct"] is not False)

    if question.get("destructive_test"):
        blocked, evidence = destructive_block_outcome(first)
        record["destructive_request_blocked"] = blocked
        record["destructive_block_evidence"] = evidence

    ordinary = question.get("category") in ("clear", "ambiguous_metric", "ambiguous_entity", "ambiguous_time")
    errors = [r for r in (first, final) if r.get("status") in ("error", "invalid_response") or (r.get("_http_status") or 0) >= 500]
    if ordinary:
        record["unexpected_system_error"] = bool(errors)
    elif question.get("destructive_test"):
        record["unexpected_system_error"] = bool(errors and not record["destructive_request_blocked"])
    else:
        record["unexpected_system_error"] = False
    if record["unexpected_system_error"]:
        message = next((r.get("message") for r in errors if r.get("message")), "Application returned an error response")
        record["error"] = safe_error(message)
    return record


def skipped_record(question: dict, reason: str) -> dict:
    record = new_record(question)
    record["evaluation_state"] = "not_run_infrastructure_stop"
    record["skip_reason"] = reason
    return record


def run_safety_validator_cases(cases: list[dict]) -> list[dict]:
    api_path = str(PROJECT_ROOT / "api")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    try:
        from _lib.safety import SQLSafetyError, validate_sql_safety
    except Exception as exc:
        return [{"case": c.get("name"), "expected_rejected": c["expected_rejected"],
                 "actual_rejected": None, "passed": None,
                 "error": f"Existing validator unavailable ({type(exc).__name__})."} for c in cases]
    output = []
    for case in cases:
        try:
            validate_sql_safety(case["sql"])
            actual, error = False, None
        except SQLSafetyError as exc:
            actual, error = True, str(exc)
        except Exception as exc:
            actual, error = None, f"Validator raised {type(exc).__name__}."
        output.append({"case": case.get("name"), "sql": case["sql"],
                       "expected_rejected": case["expected_rejected"],
                       "actual_rejected": actual,
                       "passed": actual == case["expected_rejected"] if actual is not None else None,
                       "error": safe_error(error)})
    return output


def pct(correct: int, count: int):
    return round(correct * 100.0 / count, 1) if count else None


def metric_status(count: int, excluded: int = 0) -> str:
    if excluded:
        return "PARTIAL_CONTAMINATION" if count else "CONTAMINATED"
    return "MEASURED" if count else "INSUFFICIENT_SAMPLE"


def calculate_metrics(records: list[dict], safety_results: list[dict],
                      api_call_events: list[dict]) -> tuple[dict, dict]:
    metrics, statuses, exclusions = {}, {}, {}

    def boolean_metric(name: str, count_name: str, key: str, eligible, excluded_by):
        candidates = [r for r in records if eligible(r)]
        scored = [r for r in candidates if isinstance(r.get(key), bool)]
        excluded_n = sum(excluded_by(r) for r in candidates)
        metrics[name] = pct(sum(bool(r[key]) for r in scored), len(scored))
        metrics[count_name] = len(scored)
        statuses[name] = metric_status(len(scored), excluded_n)
        exclusions[name] = {"provider_or_transport_excluded_cases": excluded_n}

    stopped = lambda r: r.get("evaluation_state") == "not_run_infrastructure_stop"
    boolean_metric("ambiguity_detection_accuracy_pct", "ambiguity_scored_count", "ambiguity_correct",
                   lambda r: r.get("expected_ambiguous") is not None,
                   lambda r: r.get("initial_provider_failure") or r.get("initial_transport_failure")
                   or stopped(r) or r.get("actual_ambiguous") is None)
    boolean_metric("clarification_prompt_quality_pct", "clarification_prompt_quality_scored_count", "clarification_prompt_quality",
                   lambda r: r.get("expected_ambiguous") is True,
                   lambda r: r.get("initial_provider_failure") or r.get("initial_transport_failure") or stopped(r))
    boolean_metric("clarification_success_rate_pct", "clarification_scored_count", "clarification_success",
                   lambda r: r.get("expected_ambiguous") is True,
                   lambda r: r.get("provider_contaminated") or r.get("transport_contaminated") or stopped(r))

    sql_cases = [r for r in records if r.get("reference_sql")]
    sql_scored = [r for r in sql_cases if isinstance(r.get("sql_correct"), bool)]
    sql_excluded = sum(bool(r.get("provider_contaminated") or r.get("transport_contaminated") or stopped(r)
                             or r.get("sql_correctness_unavailable_reason")
                             or r.get("final_response_status") in ("error", "invalid_response", "transport_error"))
                       for r in sql_cases)
    metrics["sql_correctness_pct"] = pct(sum(r["sql_correct"] for r in sql_scored), len(sql_scored))
    metrics["sql_correctness_scored_count"] = len(sql_scored)
    statuses["sql_correctness_pct"] = metric_status(len(sql_scored), sql_excluded)
    exclusions["sql_correctness_pct"] = {"provider_or_transport_or_reference_excluded_cases": sql_excluded}

    boolean_metric("direct_execution_success_rate_pct", "direct_execution_scored_count", "direct_execution_success",
                   lambda r: r.get("category") == "clear",
                   lambda r: r.get("initial_provider_failure") or r.get("initial_transport_failure") or stopped(r))
    boolean_metric("end_to_end_success_rate_pct", "end_to_end_scored_count", "end_to_end_success",
                   lambda r: r.get("category") in ("clear", "ambiguous_metric", "ambiguous_entity", "ambiguous_time"),
                   lambda r: r.get("provider_contaminated") or r.get("transport_contaminated") or stopped(r))
    boolean_metric("destructive_request_block_rate_pct", "destructive_request_scored_count", "destructive_request_blocked",
                   lambda r: r.get("destructive_test") is True,
                   lambda r: r.get("provider_contaminated") or r.get("transport_contaminated") or stopped(r))

    forbidden = [r for r in safety_results
                 if r.get("expected_rejected") is True and isinstance(r.get("passed"), bool)]
    metrics["safety_validator_rejection_rate_pct"] = pct(sum(r["passed"] for r in forbidden), len(forbidden))
    metrics["safety_validator_scored_count"] = len(forbidden)
    statuses["safety_validator_rejection_rate_pct"] = metric_status(
        len(forbidden), sum(r.get("passed") is None for r in safety_results))
    exclusions["safety_validator_rejection_rate_pct"] = {
        "unavailable_cases": sum(r.get("passed") is None for r in safety_results)}

    error_cases = [r for r in records if r.get("category") in (
        "clear", "ambiguous_metric", "ambiguous_entity", "ambiguous_time", "destructive_request")]
    error_scored = [r for r in error_cases if isinstance(r.get("unexpected_system_error"), bool)]
    error_excluded = sum(bool(r.get("provider_contaminated") or r.get("transport_contaminated") or stopped(r))
                         for r in error_cases)
    metrics["error_rate_pct"] = pct(sum(r["unexpected_system_error"] for r in error_scored), len(error_scored))
    metrics["error_scored_count"] = len(error_scored)
    statuses["error_rate_pct"] = metric_status(len(error_scored), error_excluded)
    exclusions["error_rate_pct"] = {"provider_or_transport_excluded_cases": error_excluded}

    valid_latencies = [e["latency_ms"] for e in api_call_events
                       if not e.get("provider_failure_type") and not e.get("transport_failure")]
    provider_latencies = [e["latency_ms"] for e in api_call_events if e.get("provider_failure_type")]
    metrics["avg_latency_ms"] = round(sum(valid_latencies) / len(valid_latencies), 1) if valid_latencies else None
    metrics["max_latency_ms"] = max(valid_latencies) if valid_latencies else None
    metrics["latency_scored_count"] = len(valid_latencies)
    statuses["avg_latency_ms"] = metric_status(len(valid_latencies), len(provider_latencies))
    exclusions["avg_latency_ms"] = {"provider_failed_api_calls": len(provider_latencies)}

    provider = [e for e in api_call_events if e.get("provider_failure_type")]
    call_count = len(api_call_events)
    metrics["api_request_count"] = call_count
    metrics["provider_failure_count"] = len(provider)
    metrics["provider_failure_rate_pct"] = pct(len(provider), call_count)
    metrics["quota_failure_count"] = sum(e.get("provider_failure_type") == "quota_exhausted" for e in provider)
    metrics["quota_failure_rate_pct"] = pct(metrics["quota_failure_count"], call_count)
    metrics["provider_5xx_failure_count"] = sum(e.get("provider_failure_type") in
        ("provider_server_error", "provider_unavailable") for e in provider)
    metrics["provider_5xx_failure_rate_pct"] = pct(metrics["provider_5xx_failure_count"], call_count)
    metrics["provider_other_failure_count"] = sum(e.get("provider_failure_type") not in
        ("quota_exhausted", "provider_server_error", "provider_unavailable") for e in provider)
    metrics["provider_failed_case_count"] = sum(r.get("provider_contaminated", False) for r in records)
    metrics["transport_failure_count"] = sum(e.get("transport_failure", False) for e in api_call_events)
    metrics["transport_failure_rate_pct"] = pct(metrics["transport_failure_count"], call_count)
    metrics["provider_failure_scored_count"] = call_count
    return metrics, {"application_metric_status": statuses, "excluded_case_counts": exclusions}


def check_health(base_url: str) -> dict:
    url = base_url.rstrip("/") + "/api/health"
    try:
        response = requests.get(url, timeout=5)
        try:
            body = response.json()
        except ValueError:
            body = {}
        healthy = response.ok and isinstance(body, dict) and body.get("status") == "ok"
        return {"available": healthy, "url": url, "http_status": response.status_code, "response": body}
    except requests.RequestException as exc:
        return {"available": False, "url": url, "http_status": None, "error": safe_error(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Quota-aware SmartSQL evaluation.")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="SmartSQL backend base URL")
    parser.add_argument("--delay", type=float, default=5.0, help="Quiet seconds between /api/ask requests (default: 5)")
    parser.add_argument("--project-root", type=Path, default=HERE.parent, help=argparse.SUPPRESS)
    args = parser.parse_args()
    global PROJECT_ROOT
    PROJECT_ROOT = args.project_root.resolve()
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    health = check_health(args.url)
    if not health["available"]:
        print("SmartSQL backend is not reachable. Start Flask at http://127.0.0.1:5000 and run again. No evaluation metrics were generated.")
        return 2
    try:
        questions, safety_cases = load_dataset()
        if any(q.get("reference_sql") or q.get("clarification_candidate_sql") for q in questions):
            check_reference_database()
            references = prepare_reference_results(questions)
        else:
            references = {}
    except Exception as exc:
        print(f"Evaluation preflight failed ({type(exc).__name__}). No application metrics were generated.")
        return 2

    safety_results = run_safety_validator_cases(safety_cases)
    # Prioritize this one API test so a later quota stop doesn't always leave
    # destructive-request behavior unmeasured. Keep the rest in dataset order.
    ordered_questions = sorted(questions, key=lambda q: (not bool(q.get("destructive_test")), q["id"]))
    session, pacer = requests.Session(), RequestPacer(args.delay)
    records, call_events = [], []
    stop_reason = None
    for question in ordered_questions:
        if stop_reason:
            records.append(skipped_record(question, stop_reason))
            continue
        print(f"[{question['id']:>2}] {question['category']:<22} {question['question'][:65]}")
        record = evaluate_question(session, args.url, question, pacer, references)
        records.append(record)
        call_events.extend(record["api_calls"])
        if record.get("provider_failures"):
            stop_reason = "Stopped after a surfaced provider failure; no retry was attempted."
        elif record.get("transport_contaminated"):
            stop_reason = "Stopped after an API transport failure; remaining questions were not sent."

    metrics, annotations = calculate_metrics(records, safety_results, call_events)
    attempted = sum(r.get("evaluation_state") == "attempted" for r in records)
    complete = not stop_reason and attempted == len(questions)
    result = {
        "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_status": "COMPLETE" if complete else
            "STOPPED_AFTER_PROVIDER_FAILURE" if stop_reason and "provider" in stop_reason else
            "STOPPED_AFTER_TRANSPORT_FAILURE" if stop_reason else "PARTIAL",
        "health_check": health,
        "total_questions": len(questions), "attempted_questions": attempted,
        "not_run_questions": len(questions) - attempted,
        "category_counts": dict(Counter(q["category"] for q in questions)),
        "summary": metrics, "metric_annotations": annotations,
        "provider_metrics_scope": (
            "Counts surfaced provider failures per /api/ask HTTP call. The backend may make multiple Gemini SDK calls "
            "per request and suppresses optional explanation failures, so these are not all underlying SDK calls."
        ),
        "provider_errors": [e for e in call_events if e.get("provider_failure_type")],
        "safety_validator_cases": safety_results, "results": records,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / f"results_{stamp}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nAPPLICATION / EVALUATION SUMMARY")
    for key, value in metrics.items():
        print(f"{key}: {value}")
    print("metric_statuses:", json.dumps(annotations["application_metric_status"], sort_keys=True))
    print("\nPROVIDER / INFRASTRUCTURE")
    for key in ("api_request_count", "provider_failure_count", "provider_failure_rate_pct",
                "quota_failure_count", "quota_failure_rate_pct", "provider_5xx_failure_count",
                "provider_5xx_failure_rate_pct", "provider_other_failure_count",
                "transport_failure_count", "transport_failure_rate_pct"):
        print(f"{key}: {metrics[key]}")
    print(f"evaluation_status: {result['evaluation_status']}")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
