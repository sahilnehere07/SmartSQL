# SmartSQL — Ambiguity-Aware Text-to-SQL Assistant

SmartSQL is a natural-language interface for querying an e-commerce database using plain English. It uses Google's Gemini API to interpret questions, detect ambiguity, generate SQL queries, and explain the results.

The key idea behind SmartSQL is simple: when a question has multiple reasonable interpretations, the system asks the user to clarify their intent instead of silently guessing.

## Problem Statement

Natural-language questions do not always have a single interpretation.

For example:

> Who was the best customer last month?

This could mean:
- The customer who spent the most money.
- The customer who placed the most orders.
- The customer who purchased most frequently.

Each interpretation could produce a different SQL query and a different result.

SmartSQL addresses this problem by incorporating ambiguity detection and clarification into the Text-to-SQL workflow.

## How It Works

1. The user enters a question in plain English.
2. The backend analyzes the question and checks whether clarification is required.
3. If the question is ambiguous, the application presents clarification options.
4. The system generates SQL using the original question and any clarification provided.
5. The generated SQL is validated against the application's safety rules.
6. The validated query is executed against PostgreSQL.
7. The application returns the query results with a natural-language explanation.

## Key Features

- Natural-language querying of an e-commerce database.
- Detection of ambiguous metrics, entities, and time references.
- Interactive clarification workflow.
- Structured model output using Pydantic.
- SQL safety validation before execution.
- Read-only database query execution.
- Bounded SQL repair for supported execution errors.
- Evaluation framework for measuring application behavior.
- Separation of frontend and backend responsibilities.
- Automated tests for core functionality.

## Example

**Question:**

Who was the best customer last month?

**Clarification:**

How should the application define "best customer"?

- By total spending.
- By number of orders.

After the user selects an option, SmartSQL generates a query based on the clarified intent and returns the result from the database.

## Architecture

```text
User
 |
 v
Frontend
(HTML, CSS, JavaScript)
 |
 v
Flask API
 |
 v
Gemini API
 |
 v
Structured Output
(Pydantic)
 |
 v
SQL Safety Validation
 |
 v
PostgreSQL Database
 |
 v
Query Results
 |
 v
Natural-Language Explanation
```

The frontend handles user input and displays responses. The backend handles model requests, clarification logic, SQL validation, database access, and result processing.

API keys and database credentials are configured through environment variables and must not be exposed in frontend code.

For additional implementation details, see [`docs/architecture.md`](docs/architecture.md).

## Technology Stack

| Component | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, Flask |
| Language model | Google Gemini API |
| Structured output | Pydantic |
| Database | PostgreSQL |
| SQL parsing | sqlparse |
| Testing | pytest |
| Deployment target | Vercel |

## Project Structure

```text
SmartSQL/
├── api/
│   ├── _lib/
│   │   ├── db.py
│   │   ├── llm.py
│   │   ├── models.py
│   │   ├── prompts.py
│   │   └── safety.py
│   └── index.py
├── database/
│   ├── schema.sql
│   └── seed.sql
├── docs/
│   ├── architecture.md
│   └── decision-log.md
├── evaluation/
│   ├── evaluate.py
│   └── test_questions.json
├── tests/
├── index.html
├── style.css
├── app.js
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
└── vercel.json
```

## Getting Started

### Prerequisites

Before running the application, make sure you have:

- Python installed.
- PostgreSQL installed and running.
- A Gemini API key.
- Git installed.

### 1. Clone the Repository

```bash
git clone https://github.com/sahilnehere07/SmartSQL.git
cd SmartSQL
```

### 2. Create a Virtual Environment

On Windows, run:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the project dependencies:

```bash
pip install -r requirements-dev.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env` and configure the required values.

The configuration includes:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_FALLBACK_MODEL=
DATABASE_URL=your_postgresql_connection_string
```

Use a Gemini model supported by your API account and keep the configuration consistent with the model used by the application.

Never commit `.env` or publish real API keys, database passwords, or connection strings.

### 4. Set Up PostgreSQL

Create a PostgreSQL database and load the project's schema and sample data.

For example, if your database is named `querysense`, run:

```bash
psql -U postgres -d querysense -f database/schema.sql
psql -U postgres -d querysense -f database/seed.sql
```

Configure `DATABASE_URL` in `.env` to match your local PostgreSQL setup.

The database contains five tables:

- `customers`
- `products`
- `orders`
- `order_items`
- `payments`

The repository includes sample data for local development and testing.

### 5. Start the Backend

From the project directory, run:

```bash
python api/index.py
```

The Flask backend will start for local development.

### 6. Start the Frontend

Open another terminal in the project directory and run:

```bash
python -m http.server 8000
```

Open the address displayed by the local static server in your browser.

Make sure the frontend's API configuration points to the running backend. The frontend and backend must be configured to communicate correctly during local development.

## Testing

Run the automated test suite from the project directory:

```bash
python -m pytest -q
```

**Latest recorded result: 47 tests passed.**

The tests cover core application behavior, including input handling, clarification, structured models, SQL safety, and regression cases.

Some integration tests may require environment variables and access to external services, depending on their configuration.

## Evaluation

SmartSQL includes a 30-question evaluation set covering several categories:

- Clear questions.
- Ambiguous metrics.
- Ambiguous entities.
- Ambiguous time references.
- Edge cases.

The latest recorded evaluation produced the following results:

| Metric | Result |
|---|---:|
| Ambiguity detection accuracy | 96.3% |
| Clarification prompt quality | 100% |
| Clarification success rate | 100% |
| SQL correctness | 100% |
| Direct execution success rate | 100% |
| End-to-end success rate | 100% |
| Destructive request block rate | 100% |
| Average latency | 2.51 seconds |

These results come from one controlled evaluation run. They do not guarantee the same performance on arbitrary questions or different databases.

The evaluation set is relatively small, and results may vary depending on model behavior, database contents, and API conditions.

## Security and Limitations

- Generated SQL is checked by a safety validator before execution.
- Database operations are designed to be read-only.
- SQL safety validation does not guarantee that a query correctly represents the user's intent.
- The model may occasionally misunderstand a question or generate an incorrect query.
- Ambiguity detection can miss ambiguous questions or request clarification unnecessarily.
- The evaluation set is limited in size.
- Authentication and persistent query history are not currently included.

A safety validator is one layer of protection, not a guarantee of production-grade security. Any public deployment should use appropriate access controls and a database that contains no sensitive information.

## Current Project Status

- [x] Core Text-to-SQL workflow.
- [x] Ambiguity detection.
- [x] Clarification workflow.
- [x] SQL safety validation.
- [x] PostgreSQL integration.
- [x] Automated test suite.
- [x] Evaluation framework.
- [x] GitHub repository.
- [X] Public deployment.

## Future Improvements

- Expand the evaluation set with more complex questions.
- Improve semantic verification of generated SQL.
- Improve frontend usability and error handling.
- Add query history.
- Introduce authentication and stronger production safeguards.
- Evaluate additional language models.

## Author

**Sahil Nehere**

Automation and Robotics Engineering Student

GitHub: [@sahilnehere07](https://github.com/sahilnehere07)
