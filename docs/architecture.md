# Architecture

```text
User (browser)
      |
      v
Static frontend (index.html / style.css / app.js)
served by Vercel as static files
      |
      | fetch POST /api/ask
      v
Vercel Python Function (api/index.py, a Flask app)
      |
      |---> api/_lib/llm.py --------> Gemini API (gemini-2.5-flash)
      |        (ambiguity detection + SQL generation,
      |         forced into the QueryAnalysis Pydantic schema)
      |
      |---> api/_lib/safety.py -----> independent SQL safety re-check
      |        (runs regardless of what the LLM claims about its own SQL)
      |
      |---> api/_lib/db.py ---------> PostgreSQL (Supabase, pooled connection)
               (query executed inside an explicit READ ONLY transaction,
                with a 5s statement_timeout and a row LIMIT cap)
      |
      v
JSON response -> rendered back into the chat UI
```

## Why a static frontend instead of Next.js/React

The master brief allowed either. A one-day MVP with one simple screen
(a chat box) doesn't need a component framework, client-side routing, or
a build step. Plain HTML/CSS/JS:

- deploys on Vercel with zero configuration (no `next build`, no
  Node version pinning, nothing to misconfigure),
- has nothing that can fail at build time,
- is easy for a beginner to read and modify end-to-end.

If this project grows (multiple pages, auth, saved query history across
sessions), migrating to Next.js later is a contained, well-understood
step -- it does not change the backend at all, since the backend is
already a separate API.

## Why Python (Flask) for the backend, deployed as Vercel Functions

Vercel's official Python runtime runs WSGI/ASGI apps (Flask, FastAPI,
Django) as Vercel Functions. One Flask app in `api/index.py`, routed by
`vercel.json`, keeps all backend logic in Python (matching the project's
learning goal) while staying inside Vercel's supported, documented path.

**Known serverless constraints this design works around:**

- *Cold starts & short-lived processes* -- each invocation may be a fresh
  process, so the backend never holds an in-memory session or connection
  pool across requests. Conversation state (the original question +
  clarification answer) is instead round-tripped through the frontend
  and sent back on the second API call. See `ClarificationContext` in
  `api/_lib/models.py`.
- *No persistent DB connection pool* -- a fresh `psycopg2` connection is
  opened and closed per request (`api/_lib/db.py`). To avoid exhausting
  Postgres' connection limit under concurrent requests, the hosted
  database should be reached through **Supabase's connection pooler**
  (port 6543, "Transaction" mode), not the direct connection (port 5432).
- *Ephemeral filesystem* -- nothing is written to disk at runtime; all
  state lives in Postgres or is passed through the request/response.

## Why one Flask app instead of one function per route

Vercel bills/cold-starts per function. A single Flask app handling
`/api/ask` and `/api/health` avoids paying a cold start twice and keeps
routing logic in one place Flask already does well.

## Security boundary

The browser never receives `GEMINI_API_KEY` or `DATABASE_URL`. Both live
only in Vercel's server-side environment variables and are read by
`api/_lib/llm.py` / `api/_lib/db.py`, which only run on the server.

## Read-only enforcement (three independent layers)

1. The LLM's system prompt instructs it to only ever write `SELECT`.
2. `api/_lib/safety.py` independently re-parses and re-checks the SQL
   before execution -- rejecting any write keyword, multiple statements,
   or dangerous functions, regardless of what the model claims.
3. `api/_lib/db.py` executes inside `SET TRANSACTION READ ONLY` against
   (ideally) a Postgres role that only has `SELECT` privileges, and the
   connection is rolled back, never committed.

This is layered, pragmatic protection for a portfolio project -- **not**
a claim of formal, audited SQL-injection-proof security.
