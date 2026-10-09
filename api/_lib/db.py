"""
PostgreSQL connection handling.

Important for serverless: each function invocation may get a fresh
process, so we open a short-lived connection per request rather than
pooling in-process. For the hosted database we rely on Supabase's own
connection pooler (the "Transaction" pooler on port 6543) so we don't
exhaust Postgres' connection limit under concurrent requests -- see
docs/architecture.md.
"""
from __future__ import annotations

import os
import re
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
STATEMENT_TIMEOUT_MS = 5000  # kill runaway queries after 5s


class DatabaseError(RuntimeError):
    pass


def get_connection():
    if not DATABASE_URL:
        raise DatabaseError(
            "DATABASE_URL is not set. Copy .env.example to .env locally, "
            "or set it in your Vercel project's Environment Variables."
        )
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def run_readonly_query(sql: str) -> list[dict]:
    """
    Executes `sql` inside an explicitly READ ONLY transaction, so even if
    every other safety layer somehow failed, the database itself would
    reject a write. Rolls back (never commits) regardless of outcome,
    since nothing here should ever modify data.
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql)
                rows = cur.fetchall()
        conn.rollback()  # belt-and-suspenders: never let this connection commit
        return [dict(r) for r in rows]
    except psycopg2.Error as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise DatabaseError(str(e)) from e
    finally:
        conn.close()


def find_customer_candidates(question: str) -> dict | None:
    """Return real customer rows when the question references a customer.

    The lookup is limited to common order/purchase/spending constructions so
    ordinary metric and time ambiguities do not trigger a customer table scan.
    Returns None when the question does not appear to reference a person.
    """
    patterns = (
        r"\b([A-Z][a-z'-]*\s+[A-Z][a-z'-]*)['’]s\s+"
        r"(?:orders?|purchases?|purchase history|buying history)\b",
        r"\b([A-Za-z][A-Za-z'-]*)['’]s\s+"
        r"(?:orders?|purchases?|purchase history|buying history)\b",
        r"\b(?i:has|have|did|does)\s+"
        r"([A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*)?)\s+"
        r"(?i:bought|buy|purchased|purchase|ordered|order|spent|spend)\b",
        r"\b([A-Z][a-z'-]*)\s+(?:orders?|purchases?|bought|buy|spent|spend)\b",
    )
    first_name = next(
        (match.group(1) for pattern in patterns
         if (match := re.search(pattern, question))),
        None,
    )
    if first_name is None:
        return None

    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                "SELECT id, name FROM customers "
                "WHERE lower(name) = lower(%s) "
                "OR (position(' ' in %s) = 0 "
                "AND lower(split_part(name, ' ', 1)) = lower(%s)) "
                "ORDER BY name, id",
                (first_name, first_name, first_name),
            )
            customers = [dict(row) for row in cur.fetchall()]
        conn.rollback()
        return {"first_name": first_name, "customers": customers}
    except psycopg2.Error as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise DatabaseError(str(exc)) from exc
    finally:
        if conn is not None:
            conn.close()


def get_schema_summary() -> str:
    """
    A compact, hand-written description of the schema sent to the LLM.
    Deliberately NOT auto-introspected from information_schema: sending
    only what's needed keeps the prompt small, cheap, and predictable
    (see docs/decision-log.md, "Why a hand-written schema summary?").
    """
    return """
Tables:

customers(id, name, email, signup_date, city)
products(id, name, category, price)
orders(id, customer_id -> customers.id, order_date, status, total_amount)
  status is one of: 'pending', 'completed', 'cancelled', 'refunded'
order_items(id, order_id -> orders.id, product_id -> products.id, quantity, unit_price)
payments(id, order_id -> orders.id, payment_date, amount, payment_method, status)
  payment_method is one of: 'card', 'upi', 'netbanking', 'wallet'
  status is one of: 'success', 'failed', 'refunded'

Notes:
- "Revenue" or "spending" should generally be computed from orders.total_amount
  for completed orders, unless the user specifies otherwise.
- A customer name may not be unique (multiple customers can share a first name).
""".strip()
