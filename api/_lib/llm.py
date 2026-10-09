"""
Thin wrapper around the official google-genai SDK.

Primary model: gemini-3.8-flash (set via GEMINI_MODEL in .env).

As of this writing, gemini-3.8-flash and its recent siblings (3.7/3.6
flash) are intermittently returning 503 UNAVAILABLE under provider-side
"high demand" -- confirmed by multiple independent reports, not specific
to this project's prompt/schema. Two defenses are implemented for this,
both bounded (never infinite):

  1. A short retry with backoff on the PRIMARY model.
  2. If the primary model is still 503 after retrying, ONE attempt on a
     fallback model (GEMINI_FALLBACK_MODEL, default gemini-3.5-flash-lite
     -- documented by Google as optimized specifically for low-latency,
     high-throughput reliability, making it less likely to be capacity
     constrained at the same moment as the primary model).

If you don't want fallback behavior, set GEMINI_FALLBACK_MODEL="" in
.env and it will be skipped -- the primary model's own retries still run.
"""
from __future__ import annotations

import os
import time
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import ValidationError

from .models import QueryAnalysis
from .prompts import build_system_prompt, build_user_prompt, build_explanation_prompt
from .db import DatabaseError, find_customer_candidates

PRIMARY_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")

MAX_RETRIES = 1          # retries on the PRIMARY model for invalid structured output
MAX_503_RETRIES = 2      # retries on the PRIMARY model specifically for 503s
RETRY_BACKOFF_SECONDS = [1.5, 3.0]  # bounded, increasing delay -- never infinite


class LLMError(RuntimeError):
    pass


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise LLMError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env locally, "
            "or set it in your Vercel project's Environment Variables."
        )
    return genai.Client(api_key=api_key)


def _call_gemini(client: genai.Client, model: str, system_prompt: str, user_prompt: str):
    """One raw call to generate_content with structured-output config."""
    return client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=QueryAnalysis,
            temperature=0.1,
        ),
    )


def _parse_and_validate(response) -> QueryAnalysis:
    parsed = response.parsed
    if parsed is None:
        raise LLMError("Model returned no parsed structured output.")

    analysis = (
        parsed if isinstance(parsed, QueryAnalysis)
        else QueryAnalysis.model_validate(parsed)
    )

    if analysis.is_ambiguous and not analysis.clarification_question:
        raise ValidationError.from_exception_data(
            "QueryAnalysis",
            [{"type": "missing", "loc": ("clarification_question",), "input": None}],
        )
    if not analysis.is_ambiguous and not analysis.sql_query:
        raise ValidationError.from_exception_data(
            "QueryAnalysis",
            [{"type": "missing", "loc": ("sql_query",), "input": None}],
        )
    return analysis


def _ground_customer_options(
    analysis: QueryAnalysis, customer_candidates: dict | None
) -> QueryAnalysis:
    """Prevent model-invented customer choices when real candidates are known."""
    if not analysis.is_ambiguous or customer_candidates is None:
        return analysis

    customers = customer_candidates["customers"]
    if customers:
        analysis.options = [
            f'{customer["name"]} (ID: {customer["id"]})'
            for customer in customers[:3]
        ]
        analysis.options.append("Other (enter full name, email, or customer ID)")
    else:
        first_name = customer_candidates["first_name"]
        analysis.clarification_question = (
            f"No customer named {first_name} was found. "
            "Please provide a full name, email, or customer ID."
        )
        analysis.options = ["Full name", "Email address", "Customer ID"]
    return analysis


def analyze_question(question: str, clarification=None) -> QueryAnalysis:
    client = _get_client()
    customer_candidates = None
    if clarification is None:
        try:
            customer_candidates = find_customer_candidates(question)
        except DatabaseError:
            # Candidate grounding is helpful, but an unavailable lookup must
            # not break the existing ambiguity or query-generation flow.
            pass
    system_prompt = build_system_prompt(customer_candidates)
    user_prompt = build_user_prompt(question, clarification)

    last_error: Exception | None = None

    # --- Attempt 1..N on the PRIMARY model ---
    structured_output_attempts = 0
    service_unavailable_attempts = 0

    while True:
        try:
            response = _call_gemini(client, PRIMARY_MODEL, system_prompt, user_prompt)
            return _ground_customer_options(
                _parse_and_validate(response), customer_candidates
            )

        except (ValidationError, LLMError) as e:
            last_error = e
            structured_output_attempts += 1
            if structured_output_attempts > MAX_RETRIES:
                break
            continue  # retry malformed structured output immediately

        except ServerError as e:
            last_error = e
            if service_unavailable_attempts < MAX_503_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS[min(
                    service_unavailable_attempts, len(RETRY_BACKOFF_SECONDS) - 1
                )])
                service_unavailable_attempts += 1
                continue
            break  # exhausted 503 retries on the primary model

        except ClientError as e:
            # Not transient (bad model name, bad request, auth problem) --
            # retrying never helps, fail fast with a clear message.
            raise LLMError(f"Gemini rejected the request ({e}).") from e

    # --- Primary model exhausted. Try the fallback model ONCE, only for 503s. ---
    if isinstance(last_error, ServerError) and FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
        try:
            response = _call_gemini(client, FALLBACK_MODEL, system_prompt, user_prompt)
            return _ground_customer_options(
                _parse_and_validate(response), customer_candidates
            )
        except Exception as fallback_error:
            last_error = fallback_error

    if isinstance(last_error, ServerError):
        raise LLMError(
            "Gemini is temporarily unavailable (the provider is reporting "
            "high demand on this model). This is on Google's side, not "
            "your setup -- please try again in a moment."
        )
    raise LLMError(f"Model did not return valid structured output: {last_error}")


def explain_results(question: str, sql: str, rows: list[dict]) -> str:
    client = _get_client()
    try:
        response = client.models.generate_content(
            model=PRIMARY_MODEL,
            contents=build_explanation_prompt(question, sql, rows),
            config=types.GenerateContentConfig(temperature=0.3),
        )
        text = (response.text or "").strip()
        return text or f"Found {len(rows)} matching result(s)."
    except Exception:
        # Explanation is a nice-to-have; never let it break the response
        return f"Found {len(rows)} matching result(s)."
