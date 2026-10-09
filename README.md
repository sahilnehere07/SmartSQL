# SmartSQL — Ambiguity-Aware Text-to-SQL Assistant

Ask a plain-English question about a small e-commerce database and get a
real, executed-SQL answer. Unlike a naive text-to-SQL demo, SmartSQL
first checks whether your question could reasonably mean more than one
thing — and asks you to clarify before it ever writes a query.

## Problem

A question like *"Who is the best customer?"* has no single correct SQL
translation. "Best" could mean highest total spend, most orders, or most
frequent purchases — each produces a different, equally plausible-looking
query and a different, confidently-stated answer. Most text-to-SQL demos
silently pick one interpretation. That's a real reliability problem in
production analytics tools, not just an edge case.

## Solution

SmartSQL runs every question through an **ambiguity-detection step**
before generating SQL. If the question is ambiguous — because the metric
is unclear, or because it refers to an entity (like a name) that isn't
unique in the data — it asks a clarification question with concrete
options instead of guessing. Once clarified, it generates a single
read-only SQL query, independently re-validates that it's safe, executes
it, and explains the result in plain English.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full diagram
and reasoning. Short version:

```
Browser (static HTML/JS)
   -> POST /api/ask
Vercel Python Function (Flask)
   -> Gemini API (ambiguity check + SQL generation, Pydantic-structured)
   -> independent SQL safety validator
   -> PostgreSQL (Supabase), read-only transaction
   -> JSON answer back to the browser
```

## Features

- Natural-language-to-SQL over a 5-table e-commerce schema
- Ambiguity detection (metric ambiguity, entity ambiguity, vague time ranges)
- Clarification question + follow-up workflow
- Structured LLM output via Pydantic (`response_schema`)
- Independent SQL safety validation (read-only enforced 3 separate ways)
- SQL error handling with a bounded (max 2) repair retry
- Evaluation framework producing real, measured metrics
- Clean frontend/backend/secret separation
- Deployed as a real web app on Vercel (no Streamlit)

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, vanilla JavaScript (static, no build step) |
| Backend | Python, Flask, deployed as a Vercel Function |
| AI | Gemini API (`gemini-2.5-flash`), `google-genai` SDK |
| Structured output | Pydantic v2 |
| Database | PostgreSQL (hosted on Supabase) |
| SQL parsing/safety | `sqlparse` |
| Deployment | Vercel |

---

## PART 5 — Local PostgreSQL Setup (beginner-friendly)

You only need this for local development/testing. The deployed app uses
a hosted database (Part 6).

1. **Download PostgreSQL** from
   [postgresql.org/download](https://www.postgresql.org/download/)
   and pick your OS. On Windows/Mac, use the official installer
   (EDB installer on Windows, Postgres.app or the EDB installer on Mac).
2. **During installation**, keep the default port (`5432`) and default
   username (`postgres`). When asked for a password, set one you'll
   remember — you'll need it in step 6.
3. Leave "Stack Builder" unchecked unless you want extra tools — not
   needed here.
4. **Verify it's running**: on Windows, check "Services" for
   `postgresql-x64-<version>` is "Running". On Mac/Linux:
   ```bash
   pg_isready
   ```
   You should see `accepting connections`.
5. **Open a SQL client.** Easiest beginner option: `psql`, installed
   alongside Postgres. Open a terminal and run:
   ```bash
   psql -U postgres
   ```
   Enter the password you set in step 2.
6. **Create the database**:
   ```sql
   CREATE DATABASE smartsql;
   \q
   ```
7. **Run the schema file** (from the project's root folder, in a normal
   terminal, not inside `psql`):
   ```bash
   psql -U postgres -d smartsql -f database/schema.sql
   ```
8. **Run the seed file**:
   ```bash
   psql -U postgres -d smartsql -f database/seed.sql
   ```
   (If you want to regenerate different sample data first:
   `python database/generate_seed.py`.)
9. **Verify the tables exist**:
   ```bash
   psql -U postgres -d smartsql -c "\dt"
   ```
   You should see `customers`, `products`, `orders`, `order_items`, `payments`.
10. **Run a test query**:
    ```bash
    psql -U postgres -d smartsql -c "SELECT COUNT(*) FROM customers;"
    ```
    You should get a number (40, if you used the provided seed data unmodified).

Your local connection string for `.env` will look like:
```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/smartsql
```

---

## PART 6 — Hosted Database Setup (Supabase)

The deployed app can't reach your laptop, so it needs a hosted Postgres.

1. Go to [supabase.com](https://supabase.com) and create a free account.
2. Click **New project**. Choose an organization, name it (e.g.
   `smartsql`), set a database password (save it somewhere safe), and
   pick a region close to you.
3. Wait for the project to finish provisioning (a couple of minutes).
4. Open the **SQL Editor** in the Supabase dashboard, paste the contents
   of `database/schema.sql`, and run it.
5. Open a new query, paste the contents of `database/seed.sql`, and run it.
6. Verify: in the **Table Editor**, confirm `customers`, `products`,
   `orders`, `order_items`, and `payments` all show rows.
7. Go to **Project Settings -> Database**. Under **Connection string**,
   select the **Transaction pooler** (port `6543`) — not the direct
   connection — because Vercel Functions are serverless and the pooler
   handles many short-lived connections correctly (see
   `docs/architecture.md` for why).
8. Copy that connection string. It looks like:
   ```
   postgresql://postgres.xxxxxxxx:[YOUR-PASSWORD]@aws-0-region.pooler.supabase.com:6543/postgres
   ```
   Replace `[YOUR-PASSWORD]` with your actual database password.
9. This full string is your `DATABASE_URL` — you'll add it locally to
   `.env` and later to Vercel's environment variables (Part 12).

> Verify Supabase's current free-tier limits (project pause after
> inactivity, storage cap, row limits) on their
> [pricing page](https://supabase.com/pricing) before relying on this
> for anything beyond a portfolio project — free-tier terms change.

---

## PART 7 — Gemini API Setup

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
   and sign in with a Google account.
2. Click **Create API key**, choose or create a Google Cloud project when
   prompted, and copy the generated key.
3. This project uses `gemini-2.5-flash` by default (fast, inexpensive,
   more than capable for this schema). You can override it by setting
   `GEMINI_MODEL` in `.env`.
4. **Store the key locally**: paste it into your `.env` file as
   `GEMINI_API_KEY=...` (never into any `.py` or `.js` file).
5. **Store the key in Vercel**: Project Settings -> Environment Variables
   (see Part 12).
6. **Test the key** works before building anything else:
   ```bash
   python -c "
   from google import genai
   client = genai.Client(api_key='YOUR_KEY_HERE')
   r = client.models.generate_content(model='gemini-2.5-flash', contents='Say hello in 3 words.')
   print(r.text)
   "
   ```
7. If you hit a free-tier rate limit, Gemini returns a `429` error.
   Check your current quota and pricing at
   [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
   — free-tier limits change over time, so verify rather than assume.

---

## PART 8 — Local Environment Setup (from an empty folder)

```bash
git clone <your-repo-url> smartsql-text-to-sql
cd smartsql-text-to-sql

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements-dev.txt

cp .env.example .env
# now edit .env and fill in GEMINI_API_KEY and DATABASE_URL
```

Run the backend locally:
```bash
python api/index.py
```
It starts on `http://localhost:5000`. Open `index.html` directly in a
browser, or serve the whole folder with any static server, e.g.:
```bash
python -m http.server 3000
```
and visit `http://localhost:3000`. (The frontend calls `/api/ask` —
if you're serving the static files on a different port than Flask,
either proxy `/api` to port 5000 or temporarily point `app.js`'s
`fetch` calls at `http://localhost:5000/api/ask` for local testing.)

---

## PART 9 — Local Testing

Run the offline unit tests (no DB or API key needed):
```bash
pytest tests/test_safety.py tests/test_models.py -v
```
Expected: all tests pass (21 at the time of writing — see
`tests/test_safety.py` for exactly what's covered: write-statement
rejection, stacked-query rejection, dangerous functions, LIMIT capping).

Run the integration tests (needs `DATABASE_URL` + `GEMINI_API_KEY` set,
and the schema/seed loaded):
```bash
pytest tests/test_integration.py -v
```
These are skipped automatically if the environment variables aren't set.

---

## PART 10 — Evaluation (real metrics, not invented ones)

With the backend running locally (`python api/index.py`) and `.env`
filled in:
```bash
python evaluation/evaluate.py
```
This sends all 30 questions in `evaluation/test_questions.json` to the
running API and writes `evaluation/results_<timestamp>.json` plus a
console summary with:

- **Ambiguity detection accuracy** — did the system correctly flag
  ambiguous vs. clear questions?
- **Safety-held rate** — did the destructive-intent edge case
  ("Delete all customers...") ever actually execute a write? (It
  shouldn't, ever — this is a safety regression test, not a soft metric.)
- **End-to-end success rate** — for clear questions, did the system
  return a successful answer with real rows?
- **Average / max latency**

**These numbers only exist after you run this script with your own API
key and database.** Nothing in this README states a specific percentage,
because none has been produced yet — see Part 15 for exactly how to fill
that in honestly once you have it.

---

## PART 11 — GitHub

```bash
git init
git add .
git commit -m "Initial project structure"

git add database/
git commit -m "Add PostgreSQL schema and seed data"

git add api/_lib/db.py
git commit -m "Add database connection"

git add api/_lib/llm.py api/_lib/prompts.py
git commit -m "Add Gemini integration"

git add api/_lib/models.py
git commit -m "Add Pydantic structured output"

# ...continue with:
# "Add ambiguity detection", "Add clarification workflow",
# "Add SQL safety validation", "Add error handling",
# "Add evaluation framework", "Add frontend", "Improve UI",
# "Add documentation", "Prepare Vercel deployment"
# -- commit in whatever grouping actually matches what you changed;
#    don't force a 1:1 match if your real edits don't split this cleanly.

git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```
Confirm `.env` never shows up in `git status` before your first commit —
it's covered by `.gitignore`, but double-check.

---

## PART 12 — Vercel Deployment

### A. GitHub
Push the repo (Part 11) before starting here — Vercel deploys from GitHub.

### B. Hosted database
Complete Part 6 first. Have your Supabase pooler `DATABASE_URL` ready.

### C. Gemini
Complete Part 7 first. Have your `GEMINI_API_KEY` ready.

### D. Vercel
1. Go to [vercel.com](https://vercel.com) and sign in (GitHub login is easiest).
2. Click **Add New -> Project**, and select your GitHub repository.
3. Vercel should auto-detect this as a project with a Python function in
   `/api` plus static files at the root — no framework preset needed.
4. Under **Environment Variables**, add:
   - `GEMINI_API_KEY` = your key from Part 7
   - `DATABASE_URL` = your Supabase pooler string from Part 6
   - (optional) `GEMINI_MODEL` if overriding the default
5. Click **Deploy**.
6. Once deployed, open the generated URL (e.g. `https://smartsql-xxxx.vercel.app`).
7. **Test a clear query**: "How many customers signed up last month?"
   — should return a direct answer.
8. **Test an ambiguous query**: "Who was the best customer last month?"
   — should return a clarification question with options.
9. **Test clarification**: click one of the options — should return a
   final answer referencing that metric.
10. **Test SQL execution**: expand "View generated SQL" under any
    successful answer and confirm it looks like a real, sensible query.
11. Test the database-ambiguity case: "Show me John's orders." — should
    ask which John, since the seed data has three.

---

## PART 13 — Post-Deployment Testing Checklist

- [ ] Clear query: *"How many customers signed up last month?"*
- [ ] Ambiguous metric: *"Who was the best customer last month?"*
- [ ] Database ambiguity: *"Show me John's orders."*
- [ ] Vague time: *"Show me recent orders."*
- [ ] Destructive-intent (should be refused, never executed):
      *"Delete all customers from Mumbai."*
- [ ] Nonsense input: confirm a graceful error, not a crash.
- [ ] "View generated SQL" expands/collapses correctly.
- [ ] Reload the page — old state clears (no stale clarification context).

---

## PART 14 — Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| Vercel build fails | Check the build log for a missing dependency in `requirements.txt`, or a Python syntax error. Confirm `vercel.json`'s rewrite path matches `api/index.py` exactly. |
| "GEMINI_API_KEY is not set" in deployed app | Env var wasn't added in Vercel, or was added after the last deploy — redeploy after adding it. |
| "DATABASE_URL is not set" | Same as above, for the database variable. |
| Database connection fails / times out | Make sure you used the **pooler** connection string (port 6543), not the direct one (5432) — serverless functions opening many direct connections can exhaust Postgres' connection limit. |
| CORS errors in the browser console | Shouldn't normally occur since the frontend and API are served from the same Vercel domain. If you split them across domains, you'll need to add CORS headers in `api/index.py` (Flask-CORS). |
| "Only SELECT statements are allowed" errors often | The model is generating non-SELECT SQL for valid questions — check `api/_lib/prompts.py`'s schema description is accurate and not confusing the model. |
| Gemini `429` / rate limit errors | You've hit the free-tier quota. Wait for it to reset, or check current limits at ai.google.dev. The evaluation script will show this as failures, not silently skip them. |
| Frontend loads but nothing happens on submit | Open browser dev tools -> Network tab, check the `/api/ask` request/response. Most often a JSON parsing or environment variable issue on the backend (see above). |
| Deployment works, but every query fails with a database error | Confirm `schema.sql` and `seed.sql` were actually run against the **same** Supabase project you pointed `DATABASE_URL` at. |

---

## PART 15 — Filling In Real Results

Once you've run `evaluation/evaluate.py` against your own deployment,
replace this section with your actual output, e.g.:

```
Ambiguity detection accuracy: XX% (n = NN)
Safety-held rate: 100% (n = 1 — destructive-intent edge case)
End-to-end success rate: XX% (n = NN)
Average latency: X.X seconds
```

**Live demo:** `YOUR_VERCEL_URL`

## Limitations (be honest about these)

- Safety validation is a pragmatic layered defense for a portfolio
  project, not an audited, production-grade SQL-injection guard.
- Ambiguity detection depends entirely on the LLM's judgment calls —
  it will occasionally over-flag or under-flag a question; the
  evaluation framework exists specifically to measure how often.
- No authentication — anyone with the URL can query the (synthetic,
  non-sensitive) sample database.
- No persistent query history across sessions/devices.
- The explanation step (`explain_results`) makes a second Gemini call
  per successful query, which adds latency and token cost.

## Future improvements

- Stronger semantic validation of generated SQL (e.g. a second LLM pass
  that checks the SQL actually answers the clarified question)
- Expand the evaluation set toward 50 questions
- Query result caching for repeated questions
- Side-by-side comparison of multiple models' SQL quality
- Basic authentication for a non-public deployment
- Structured logging/observability for production use

---

## PART 16 — Interview Preparation

**1. Why does your system ask clarifying questions instead of just answering?**
Because words like "best" or a name like "John" can map to more than one
reasonable SQL query. Picking one silently risks a confidently wrong
answer; asking costs one extra round-trip but guarantees the SQL matches
what the user actually meant.

**2. How do you detect ambiguity?**
The LLM is prompted to classify the question against the schema and
return a structured `QueryAnalysis` object (via Pydantic). It's trained
via the prompt to flag metric ambiguity ("best" by what measure),
entity ambiguity (a name matching multiple rows), and vague time ranges.

**3. What happens if the model hallucinates a column that doesn't exist?**
The query would fail at execution (Postgres raises an error) rather than
silently returning wrong data, since we don't validate column existence
client-side before execution — that's a current limitation. The error
handling layer catches it and reports a clear message rather than crashing.

**4. How do you guarantee the system never writes to the database?**
Three independent layers: the prompt instructs SELECT-only; an
independent validator re-parses the SQL and rejects any write keyword,
multiple statements, or dangerous function regardless of what the model
claims; and the DB transaction itself is explicitly set read-only.

**5. Why Pydantic instead of just asking the model to "return JSON"?**
Free-text JSON from a model can be malformed or drift in shape over
time. `response_schema` with a Pydantic model constrains the output at
the API level and lets the code validate/retry deterministically instead
of regex-parsing prose.

**6. Why not use an ORM instead of raw SQL execution?**
The entire point of the project is *generating* SQL from natural
language — an ORM would hide the thing being demonstrated. Raw,
validated SQL execution keeps the generated query visible and auditable.

**7. How did you handle the serverless / cold-start problem?**
No per-request in-memory state; conversation context (original question
+ clarification answer) round-trips through the frontend. The database
connection is opened and closed per request, routed through Supabase's
connection pooler rather than relying on a persistent app-side pool.

**8. What would you change for a real production version?**
Add authentication, column/type validation of generated SQL before
execution, structured logging, a larger evaluation set with human review,
and likely a semantic-correctness check beyond syntactic safety.

**9. How did you measure whether the clarification step actually helps?**
The evaluation script scores ambiguity-detection accuracy and
end-to-end success rate against a fixed, versioned question set
(`evaluation/test_questions.json`) — same questions, same expected
categories, run against the live system so the numbers are reproducible.

**10. What was the hardest part?**
Making ambiguity detection neither over-trigger (asking for clarification
on genuinely clear questions, which is annoying) nor under-trigger
(missing real ambiguity, which is the whole problem this project exists
to solve) — tuned through the prompt and the evaluation question set's
`edge_case` category.

---

## PART 17 — Resume Bullets

Use placeholders until you've run your own evaluation (Part 15) and have
real numbers to put in.

- Built SmartSQL, an ambiguity-aware text-to-SQL assistant that detects
  when a natural-language question has multiple valid interpretations
  and asks a clarifying question before generating SQL, achieving
  **XX% ambiguity-detection accuracy** across a 30-question evaluation set.

- Designed a three-layer SQL safety system (prompt constraint,
  independent static validation, read-only DB transaction) enforcing
  zero write access for an LLM-driven SQL generation pipeline; deployed
  on Vercel with a hosted PostgreSQL (Supabase) backend.

- Implemented structured LLM output using Pydantic and Gemini's
  `response_schema`, replacing free-text parsing with a validated,
  retry-bounded contract between the model and the backend.
