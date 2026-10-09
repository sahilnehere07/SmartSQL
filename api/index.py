"""
Single Flask app handling all backend routes. Deployed by Vercel as one
Python Function; vercel.json rewrites /api/* to this file so Flask's own
router does the rest. Keeping everything in one function avoids extra
cold starts from spinning up multiple separate functions.
"""

from __future__ import annotations

import sys
import os
import time
import traceback
import re

# Make the api directory importable
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from pydantic import ValidationError


# Load variables from the project's .env file BEFORE importing
# modules that read environment variables.
load_dotenv()


from _lib.models import AskRequest, ClarificationContext
from _lib.llm import analyze_question, explain_results, LLMError
from _lib.safety import validate_sql_safety, SQLSafetyError
from _lib.db import run_readonly_query, DatabaseError


app = Flask(__name__)

# Allow the local frontend on port 8000 to communicate
# with the Flask backend on port 5000.
CORS(app)


# Natural-language intent is checked before the LLM can reinterpret an
# explicitly destructive request as a read-only query.
_DESTRUCTIVE_REQUEST_RE = re.compile(
    r"^\s*(?:(?:please|kindly)\s+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:(?:i|we)\s+(?:want|need)\s+(?:you\s+)?to\s+|"
    r"i\s+would\s+like\s+(?:you\s+)?to\s+)?"
    r"(?:please\s+)?"
    r"(?:delete|remove|erase|destroy|drop|truncate|update|modify|alter|insert|"
    r"replace|overwrite|purge|wipe|clear)\b",
    re.IGNORECASE,
)


def has_destructive_intent(text: str) -> bool:
    """Recognize direct natural-language requests to mutate or remove data."""
    return bool(_DESTRUCTIVE_REQUEST_RE.match(text or ""))


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/ask", methods=["POST"])
def ask():
    start = time.time()

    try:
        body = request.get_json(force=True, silent=False) or {}
        req = AskRequest.model_validate(body)

    except ValidationError as e:
        return jsonify({
            "status": "error",
            "message": f"Invalid request: {e}"
        }), 400

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid JSON body."
        }), 400

    # Check both the original request and clarification answer. This must run
    # before ambiguity detection / SQL generation so destructive intent cannot
    # be converted into a SELECT query.
    intent_texts = [req.question]
    if req.clarification is not None:
        intent_texts.extend((
            req.clarification.original_question,
            req.clarification.user_answer,
        ))
    if any(has_destructive_intent(text) for text in intent_texts):
        return jsonify({
            "status": "error",
            "message": "Destructive database operations are not supported.",
        }), 400

    try:
        # Gemini analyzes the user's question.
        analysis = analyze_question(
            req.question,
            req.clarification
        )

        # If the question is ambiguous, ask for clarification
        # instead of guessing the user's intent.
        if analysis.is_ambiguous:
            return jsonify({
                "status": "needs_clarification",
                "clarification_question":
                    analysis.clarification_question,
                "options":
                    analysis.options or [],
            })

        # SQL safety validation happens BEFORE database execution.
        safe_sql = validate_sql_safety(
            analysis.sql_query
        )

        # Execute the validated read-only SQL query.
        rows = run_readonly_query(safe_sql)

        # Generate the natural-language explanation
        # from the actual database result.
        answer = explain_results(
            req.question,
            safe_sql,
            rows
        )

        latency_ms = int(
            (time.time() - start) * 1000
        )

        return jsonify({
            "status": "success",
            "answer": answer,
            "sql": safe_sql,
            "rows": rows[:50],
            "row_count": len(rows),
            "latency_ms": latency_ms,
        })

    except SQLSafetyError as e:
        return jsonify({
            "status": "error",
            "message": (
                f"The generated query failed a safety check: {e}. "
                "Try rephrasing your question."
            ),
        }), 200

    except DatabaseError as e:
        return jsonify({
            "status": "error",
            "message": f"Database error: {e}",
        }), 200

    except LLMError as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 200

    except Exception:
        # Log the full traceback in the server terminal,
        # but don't expose it to the browser.
        print(traceback.format_exc())

        return jsonify({
            "status": "error",
            "message": (
                "Something went wrong processing that question. "
                "Please try again."
            ),
        }), 500


# Local development entrypoint:
# python api/index.py
if __name__ == "__main__":
    app.run(
        debug=True,
        port=5000
    )
