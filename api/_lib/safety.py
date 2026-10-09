"""
SQL safety validation.

This is NOT a claim of production-grade SQL injection protection.
It is a pragmatic, documented defense-in-depth layer for a portfolio
project:

  1. The LLM is instructed to only write SELECT statements.
  2. This validator independently re-checks that before execution
     (never trust the model's own claim about its output).
  3. The database connection itself uses a read-only Postgres role
     and a transaction explicitly set to READ ONLY (see db.py), so
     even if steps 1-2 were bypassed, the database itself refuses
     writes.

Three independent layers > relying on any single one.
"""
from __future__ import annotations

import re
import sqlparse

FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "MERGE", "CALL", "COPY", "VACUUM",
    "REINDEX", "EXECUTE", "DO", "LOCK",
}

# Statement-level functions that could be abused even inside a SELECT
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_ls_dir", "dblink", "lo_import",
    "lo_export", "pg_terminate_backend", "pg_cancel_backend",
}

MAX_ROW_LIMIT = 500


class SQLSafetyError(ValueError):
    pass


def validate_sql_safety(sql: str) -> str:
    """
    Raises SQLSafetyError if the SQL is not a safe, single, read-only
    SELECT statement. Otherwise returns a (possibly LIMIT-capped)
    version of the query, ready to execute.
    """
    if not sql or not sql.strip():
        raise SQLSafetyError("Empty SQL query.")

    statements = [s for s in sqlparse.parse(sql) if s.token_first(skip_cm=True)]
    if len(statements) != 1:
        raise SQLSafetyError(
            f"Expected exactly 1 SQL statement, found {len(statements)}."
        )
    stmt = statements[0]

    stmt_type = stmt.get_type()  # e.g. "SELECT", "UNKNOWN", "INSERT"
    if stmt_type not in ("SELECT", "UNKNOWN"):
        # UNKNOWN covers things like a bare CTE ("WITH ... SELECT ...")
        # that sqlparse doesn't classify as SELECT; we double-check the
        # leading keyword for those below.
        raise SQLSafetyError(f"Only SELECT statements are allowed, got: {stmt_type}")

    normalized = sql.strip()
    first_word = normalized.split(None, 1)[0].upper() if normalized else ""
    if first_word not in ("SELECT", "WITH"):
        raise SQLSafetyError(
            f"Query must start with SELECT or WITH, starts with: {first_word!r}"
        )

    # Reject semicolon-separated multiple statements (stacked queries)
    stripped = normalized.rstrip(";")
    if ";" in stripped:
        raise SQLSafetyError("Multiple statements / stacked queries are not allowed.")

    upper_sql = normalized.upper()
    tokens = re.findall(r"[A-Za-z_]+", upper_sql)
    token_set = set(tokens)

    hit = token_set & FORBIDDEN_KEYWORDS
    if hit:
        raise SQLSafetyError(f"Forbidden keyword(s) detected: {', '.join(sorted(hit))}")

    lowered = normalized.lower()
    for fn in FORBIDDEN_FUNCTIONS:
        if fn in lowered:
            raise SQLSafetyError(f"Forbidden function detected: {fn}")

    if "--" in normalized or "/*" in normalized:
        raise SQLSafetyError("SQL comments are not allowed in generated queries.")

    # Enforce a row cap so one query can't return the entire database
    if not re.search(r"\bLIMIT\s+\d+\b", upper_sql):
        normalized = f"{stripped}\nLIMIT {MAX_ROW_LIMIT}"
    else:
        # cap an overly large explicit LIMIT down to MAX_ROW_LIMIT
        def _cap_limit(match: re.Match) -> str:
            n = int(match.group(1))
            return f"LIMIT {min(n, MAX_ROW_LIMIT)}"

        normalized = re.sub(r"LIMIT\s+(\d+)", _cap_limit, normalized, flags=re.IGNORECASE)

    return normalized
