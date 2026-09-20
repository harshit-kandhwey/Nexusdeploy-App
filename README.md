# Nexusdeploy App

A project-management REST API (teams, projects, tasks, comments, role-based access, audit log) with asynchronous background workers. Built to be a **realistic, production-style workload** that is portable across clouds, on-prem and local machines, and shipped as container images that any platform can run. Deployment infrastructure lives in other repos; see [Repository boundaries](#repository-boundaries).

> **Status:** the API, workers, migrations and container images are working today. Portability adapters and the extended feature set are planned. This README marks what is **current** versus **planned**.

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Repository boundaries](#repository-boundaries)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Development](#development)
- [Using the images](#using-the-images)
- [Documentation](#documentation)
- [Roadmap](#roadmap)

## What it does

| Capability | Status |
| --- | --- |
| Register, login, refresh, logout with JWT and server-side revocation | Current |
| Users, teams, projects, tasks, comments | Current |
| Roles (`admin`, `manager`, `developer`) enforced per endpoint | Current |
| Audit log of every mutation and failed login | Current |
| Redis-backed rate limiting, caching and token revocation | Current |
| Celery workers, per-slot queues | Current |
| Email notifications | Stub only (logs, sends nothing) |
| Prometheus metrics, OpenTelemetry tracing, Swagger UI | Current |
| Alembic migrations with concurrency-safe upgrade | Current |
| Attachments, notifications, scheduled jobs, search, webhooks, analytics | Planned |
| Pluggable storage / email / search backends (cloud-neutral) | Planned |
| Proxy-aware config (trusted hop count) | Planned |
| Deployment infrastructure (Terraform, Helm chart, GitOps, ECS) | Not in this repo; see [Repository boundaries](#repository-boundaries) |
| `docker-compose.yml` for local development | Partial: exists, but does not create the schema yet, so run `python -m src.init_db` against it first |

## Architecture

```
            clients
               │  HTTPS
        ┌──────▼───────┐
        │ Ingress / LB │  terminates TLS
        └──────┬───────┘
               │ HTTP :5000
        ┌──────▼───────┐        ┌───────────┐
        │  api (Flask, │───────▶│ PostgreSQL│
        │  gunicorn)   │        └───────────┘
        └──┬────────┬──┘        ┌───────────┐
           │        └──────────▶│   Redis   │  cache · JWT blocklist ·
           │ enqueue            └─────▲─────┘  rate limits · Celery broker
        ┌──▼───────┐                  │
        │  worker  │──────────────────┘
        │ (Celery) │
        └──────────┘   ┌────────┐
        ┌──────────┐   │  beat  │  singleton scheduler
        │ init job │   └────────┘
        └──────────┘  one-shot: DB role, migrations, seed
```

One codebase, three long-running roles (`api`, `worker`, `beat`) and one-shot operations tasks. See [docs/INTEGRATION.md](docs/INTEGRATION.md) for the full contract.

**Repository layout (current)**

```
src/
  app.py            application factory, health, metrics
  config.py         environment-driven configuration
  extensions.py     Flask/Celery/Redis extension objects
  api/v1/           auth, users, teams, projects, tasks, comments
  models/           SQLAlchemy models
  services/         cache and S3 helpers
  tasks/            Celery tasks
  utils/            validation, pagination, decorators
  init_db.py        DB role + migrations + seed (one-shot)
  smoke_test.py     post-deploy check (one-shot)
  queue_depth_check.py   blue/green cutover gate (one-shot)
migrations/         Alembic
tests/              pytest suite
Dockerfile          api image
Dockerfile.worker   worker/beat image
docs/               integration contract
```

## Repository boundaries

Three related repos, one owner each:

| Repo | Owns |
| --- | --- |
| **Nexusdeploy-App** (this repo) | All application code: the Flask API, Celery worker and beat, migrations, the Dockerfile, the image build, and the workload contract (entrypoints, `/health`, `/ready`, `/metrics`, env vars, one-shot tasks such as `src.init_db`, `src.smoke_test`, `src.queue_depth_check`) |
| [Ephemeral-Deploy](https://github.com/harshit-kandhwey/Ephemeral-Deploy) | The AWS ECS infrastructure code (Terraform, blue-green, CI/CD, monitoring) that deploys this workload |
| [Kubeforge](https://github.com/harshit-kandhwey/Kubeforge) | The Kubernetes infrastructure code (Terraform, Helm chart, GitOps, cluster config) that deploys this workload |

Infra repos consume this repo only as container images and the documented contract ([docs/INTEGRATION.md](docs/INTEGRATION.md)); they never edit its code. This repo contains no infrastructure or deployment code for any deployed target (ECS, Kubernetes). A local-development compose file is the one exception.

## Quick start

Requires Python 3.11+, PostgreSQL (or SQLite for a quick look) and Redis.

```bash
pip install -r requirements.txt

# Redis on localhost:6379 is required (rate limiting, JWT revocation, cache, Celery).
export ENV=development
python -m src.init_db          # creates the schema and seeds demo users (development only)
python -m src.run              # dev server on http://localhost:5000
```

Then:

```bash
curl -s localhost:5000/health
curl -s -X POST localhost:5000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"ChangeMe-Admin-2024!"}'
```

Seeded demo users (development only): `admin`, `manager`, `developer1`, `developer2`. The demo passwords are defaults for local use and **must not** be used elsewhere. In any non-development environment, seeding refuses to run without `SEED_*_PASSWORD` set.

Swagger UI is at `/apidocs` when `ENABLE_SWAGGER=true` (default in development, off in production).

To run a worker locally:

```bash
celery -A src.celery_worker:celery worker --loglevel=info
```

A `docker-compose.yml` (Postgres, Redis, api, worker, beat, redis-commander) exists but does not yet run the schema initialiser, so run `python -m src.init_db` against it yourself for now. Adding an init service, MinIO and MailHog is planned.

## Configuration

Everything is an environment variable. Production fails fast at startup and lists every missing required variable.

| Variable | Required | Purpose |
| --- | --- | --- |
| `ENV` | Yes | `development` \| `production` \| `testing` |
| `DATABASE_URL` | Production | PostgreSQL connection string |
| `SECRET_KEY` | Production | Flask secret (32+ random bytes) |
| `JWT_SECRET_KEY` | Recommended | JWT signing key (falls back to `SECRET_KEY`) |
| `REDIS_URL` | No | Cache and token blocklist (default `redis://localhost:6379/0`) |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | No | Celery (default Redis) |
| `CELERY_TASK_QUEUE` | No | Queue name for this deployment or slot |
| `CORS_ORIGINS` | No | Comma-separated allowed browser origins |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | Enables tracing |

The complete reference, including database-init and seed variables, is in [docs/INTEGRATION.md](docs/INTEGRATION.md#3-configuration-environment-variables).

## API overview

Base path `/api/v1`, JSON, `Authorization: Bearer <token>`.

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/register` · `login` · `refresh` · `logout` · `GET /auth/me` |
| Users | `GET /users` · `GET/PUT/DELETE /users/{id}` |
| Teams | `GET/POST /teams` · `GET/PUT /teams/{id}` |
| Projects | `GET/POST /projects` · `GET/PUT /projects/{id}` |
| Tasks | `GET/POST /tasks` · `GET/PUT/DELETE /tasks/{id}` |
| Comments | `GET/POST /tasks/{id}/comments` |

Operational endpoints: `GET /health`, `/ready`, `/metrics`.

## Development

```bash
# tests: in-memory SQLite, and Redis on localhost:6379 (DB 15)
pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=85

# a single test
pytest tests/test_auth.py::test_login_success -v

# lint and static analysis (same as CI)
flake8 . --max-line-length=120 --exclude=migrations/
black --check . && isort --check .
bandit -r src/ -ll
```

Schema changes: edit the model, then create and review a migration.

```bash
flask db migrate -m "describe the change"
```

Migrations must be backwards compatible with the previous release (expand, then contract), because rolling and blue/green deployments run two versions at once.

## Using the images

The app is consumed **as container images**; it contains no cloud-specific code.

- `Dockerfile`: API (gunicorn on `:5000`).
- `Dockerfile.worker`: Celery worker and beat.
- Distroless, non-root (uid 65532). No shell: command overrides start with `-m <module>` or a script path, never `python`.

Before first start and on every release, run the one-shot initialiser:

```bash
python -m src.init_db     # in a container: override the command to  -m src.init_db
```

For probes, ingress and proxy settings, scaling, blue/green, and what an orchestrator must supply, read **[docs/INTEGRATION.md](docs/INTEGRATION.md)**. That document is written for the people and repositories that deploy this app.

Deploying them is the job of the infrastructure repos listed under [Repository boundaries](#repository-boundaries); any other infrastructure repo should consume the same contract.

## Documentation

| Document | Audience | Contents |
| --- | --- | --- |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Infra / platform engineers | Roles, dependencies, env vars, endpoints, probes, images, migrations, scaling, security |

## Roadmap

Phase 0 makes the app portable and easy for any orchestrator to run (proxy awareness, liveness/readiness split, graceful shutdown, JSON logs, secret files). Phase 1 finishes attachments, notifications, scheduled jobs and auth hardening. Later phases add search, webhooks, real-time updates, multi-tenancy and analytics.
