# Contributing

## Setup

```bash
pip install -r requirements.txt
docker compose up -d          # postgres + redis + api + worker + beat + redis-commander
```

API: http://localhost:5000, Swagger UI: http://localhost:5000/apidocs.
Copy `.env.example` to `.env` for running outside Docker.

## Before opening a PR

```bash
pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=85   # needs Redis on :6379 (DB 15)
flake8 . --max-line-length=120 --exclude=migrations/ && black --check . && isort --check . && bandit -r src/ -ll
```

- Tests run on in-memory SQLite by default. To run them on Postgres (as CI does), set
  `TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/nexusdeploy_test`. The name must
  contain a `test` token: the suite drops every table it creates and refuses to run
  against anything else. `DATABASE_URL` is never used for tests.
- A model change needs `flask db migrate -m "..."`; commit the generated revision.
- A new guard/test isn't trusted until you have watched it fail on the bug it
  catches (plant the bug, see red, remove it, see green).
- Changes to the contract with the infrastructure repo (image entrypoints,
  env vars, health endpoints, the one-shot `src.*` tasks — see `README.md`)
  must land in both repos together.
