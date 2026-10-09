# Decision Log

### Why clarification instead of just generating SQL?

Ambiguous questions ("best customer", "top products") have more than one
reasonable SQL interpretation. A system that silently picks one produces
a confidently-wrong answer indistinguishable from a correct one. Asking
is slower but trustworthy -- which matters more for an analytics tool.

### Why Pydantic structured output instead of parsing free-form text?

Free-form LLM text requires fragile string parsing (regex for "yes this
is ambiguous", hoping the model always phrases things the same way).
Forcing the response into a fixed `QueryAnalysis` schema via Gemini's
`response_schema` makes the output machine-readable and lets the backend
fail loudly (and retry once) if the model doesn't comply, instead of
silently misinterpreting malformed text.

### Why a hand-written schema summary instead of introspecting the DB live?

`information_schema` introspection would send more tokens (cost, latency)
and could leak internal details (exact index names, unrelated columns)
the model doesn't need. A hand-written summary in `api/_lib/db.py` is
small, reviewable, and lets us annotate business meaning ("revenue"
usually means completed orders) that raw column names don't convey.

### Why PostgreSQL?

A realistic relational schema (customers / orders / order_items /
payments with foreign keys) is what makes ambiguity *actually* occur --
e.g. multiple customers can share a first name. A single flat table or a
document store wouldn't produce the same class of problem.

### Why read-only execution, enforced three separate ways?

The app's only job is answering analytical questions. It should be
structurally incapable of modifying data, not just "prompted" not to.
See `docs/architecture.md` for the three layers (prompt instruction,
independent static validation, read-only DB transaction).

### Why Vercel + Supabase instead of Streamlit?

Streamlit apps run as a single long-lived Python process and don't
represent a typical production frontend/backend split. Vercel + a hosted
Postgres (Supabase) forces a real separation between a public frontend,
a server-side API that holds secrets, and a database -- the same shape
as most real AI products.

### Why gemini-2.5-flash over a larger model?

The SQL this project needs (joins across 5 tables, simple aggregates) is
well within a fast/cheap model's ability. A larger, slower, more
expensive model would not meaningfully improve accuracy here, and this
project is explicitly cost-sensitive (₹0 target).

### Why a maximum of one retry on invalid structured output (not infinite)?

An infinite retry loop on a model that reliably fails to comply would
hang the request and silently burn API quota. One retry catches
transient formatting slips; anything beyond that should surface as a
clear error, not a stuck request.
