"""
Pure unit tests -- no live database or API needed.
Run with: pytest tests/test_safety.py -v
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from _lib.safety import validate_sql_safety, SQLSafetyError, MAX_ROW_LIMIT


def test_allows_simple_select():
    sql = "SELECT id, name FROM customers WHERE city = 'Mumbai'"
    result = validate_sql_safety(sql)
    assert result.startswith("SELECT")
    assert f"LIMIT {MAX_ROW_LIMIT}" in result


def test_allows_with_cte():
    sql = "WITH t AS (SELECT * FROM orders) SELECT * FROM t"
    result = validate_sql_safety(sql)
    assert "LIMIT" in result


def test_caps_oversized_limit():
    sql = "SELECT * FROM orders LIMIT 100000"
    result = validate_sql_safety(sql)
    assert f"LIMIT {MAX_ROW_LIMIT}" in result
    assert "100000" not in result


def test_keeps_small_limit():
    sql = "SELECT * FROM orders LIMIT 5"
    result = validate_sql_safety(sql)
    assert "LIMIT 5" in result


@pytest.mark.parametrize("sql", [
    "INSERT INTO customers (name) VALUES ('x')",
    "UPDATE orders SET status = 'cancelled'",
    "DELETE FROM customers",
    "DROP TABLE customers",
    "TRUNCATE orders",
    "ALTER TABLE customers ADD COLUMN x TEXT",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON customers TO public",
])
def test_rejects_write_statements(sql):
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_rejects_stacked_queries():
    sql = "SELECT * FROM customers; DROP TABLE customers;"
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_rejects_multiple_select_statements():
    sql = "SELECT 1; SELECT 2;"
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_rejects_sql_comments():
    sql = "SELECT * FROM customers -- comment"
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_rejects_dangerous_functions():
    sql = "SELECT pg_sleep(10)"
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_rejects_empty_query():
    with pytest.raises(SQLSafetyError):
        validate_sql_safety("")


def test_rejects_non_select_start():
    sql = "EXPLAIN SELECT * FROM customers"
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)
