# Integration Guide (for infrastructure / platform repos)

This document is the contract between Nexusdeploy App and whatever deploys it: Terraform, Helm, Kubernetes manifests, ECS task definitions, Nomad, docker-compose, or a plain VM. It describes **what the app needs from its environment** and **what it exposes**. It contains no cloud-specific instructions on purpose. Kubernetes deployment (Helm chart, GitOps, cluster config) is owned by [Kubeforge](https://github.com/harshit-kandhwey/Kubeforge); this repo contains no chart, manifests or `deploy/` directory.

Status legend: **Current** = true of the code today. **Planned** = intended, and not implemented in this repo yet. Do not build against **Planned** items until they are marked Current.

If you change anything in this document, change the app and the consuming repo together.

---

## 1. Runtime roles

One codebase, three roles. Each is a separate process/container from the same source.

| Role | Purpose | Scaling | Image |
| --- | --- | --- | --- |
| **api** | Flask API served by gunicorn on port `5000` | Horizontal, stateless | `Dockerfile` |
| **worker** | Celery worker consuming `CELERY_TASK_QUEUE` | Horizontal | `Dockerfile.worker` |
| **beat** | Celery beat scheduler | **Exactly one replica** (singleton) | `Dockerfile.worker` |

Beat must never run with more than one replica: duplicate schedulers enqueue duplicate jobs. Use a `Recreate`/stop-before-start rollout, not a rolling one.

## 2. Dependencies the app needs

| Dependency | Required | Notes |
| --- | --- | --- |
| PostgreSQL ≥ 13 | Yes (production) | SQLite is used only for tests and local dev. |
| Redis ≥ 6 | Yes | Used for: JWT revocation list, rate-limit storage, cache, and (by default) the Celery broker and result backend. |
| Object storage | Attachments (Planned) | Currently boto3/S3 only, and no endpoint uses it yet. Planned: pluggable `local\|s3\|azure\|gcs`. |
| SMTP / email provider | Notifications (Planned) | Currently three Celery tasks (`send_task_assignment_email`, `send_comment_notification`, `send_daily_digest`) whose bodies are stubs: they only write a log line, nothing is sent. Planned: pluggable backend, SMTP by default. |
| OTLP collector | Optional | Tracing is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. |

### Redis is used for four separate things

| Purpose | Setting | Default DB |
| --- | --- | --- |
| Cache / JWT blocklist | `REDIS_URL` | `/0` |
| Celery broker | `CELERY_BROKER_URL` | `/0` |
| Celery results | `CELERY_RESULT_BACKEND` | `/0` |
| Rate-limit counters | `RATELIMIT_REDIS_URL` (falls back to `REDIS_URL` with the DB index replaced by `/1`) | `/1` |

If Redis is unavailable: every request carrying a JWT is **rejected** (the blocklist cannot be checked, so auth fails closed), logout returns 503, `/health` returns 503, and the rate limiter fails **open**.

## 3. Configuration (environment variables)

All configuration is via environment variables. `ENV` selects the config class.

### Required in `ENV=production`

The app refuses to start and logs every missing variable at once.

| Variable | Sensitive | Meaning |
| --- | --- | --- |
| `ENV` | No | `production` \| `development` \| `testing`. Note `wsgi.py` defaults to **production** if unset; `celery_worker.py` and `init_db.py` default to **development**. Always set it explicitly. |
| `DATABASE_URL` | May embed credentials | SQLAlchemy URL, e.g. `postgresql://user:pass@host:5432/db` |
| `SECRET_KEY` | Secret | Flask secret. ≥ 32 random bytes. |
| `JWT_SECRET_KEY` | Secret | JWT signing key. Falls back to `SECRET_KEY` if unset; set separately in production. |

### Common

| Variable | Sensitive | Default | Meaning |
| --- | --- | --- | --- |
| `REDIS_URL` | May embed credentials | `redis://localhost:6379/0` | Cache and JWT blocklist |
| `CELERY_BROKER_URL` | May embed credentials | `redis://localhost:6379/0` | Celery broker |
| `CELERY_RESULT_BACKEND` | May embed credentials | `redis://localhost:6379/0` | Celery results |
| `CELERY_TASK_QUEUE` | No | `celery` | Queue this deployment publishes to and its worker consumes. Set a distinct value per blue/green slot or per tenant to isolate versions. |
| `RATELIMIT_REDIS_URL` | May embed credentials | derived from `REDIS_URL` | Rate-limit storage |
| `CORS_ORIGINS` | No | _(empty)_ | Comma-separated browser origin allowlist. Empty = no cross-origin access. |
| `ENABLE_SWAGGER` | No | `false` in production | Exposes `/apidocs` |
| `VERSION` | No | `unknown` | Reported by `/`, `/health`. Set to the image tag / git SHA. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | _(unset)_ | Enables tracing when set |
| `OTEL_SERVICE_NAME` | No | `nexusdeploy` | Service name reported in traces. Read only when tracing is enabled. |
| `FLASK_DEBUG`, `SQLALCHEMY_ECHO` | No | on | Read only when `ENV=development`; both default to **on**. `SQLALCHEMY_ECHO` logs every SQL statement with its bind parameters, which include password hashes and emails. |
| `AWS_REGION`, `S3_BUCKET` | No | `us-east-1`, `nexusdeploy-attachments` | **Legacy**, S3 only. No endpoint reads them (see below). Replaced by provider-neutral storage settings (Planned). |

**Sensitive:** **Secret** = a credential or signing key. **May embed credentials** = a URL that can carry a password in its userinfo (the app redacts these when it logs them). **No** = plain configuration. Treat the first two kinds as secrets.

`S3_BUCKET` and `AWS_REGION` are optional: with `S3_BUCKET` unset the app starts, and `/health` and `/ready` return 200. No API endpoint calls S3 today.

### Database initialisation (`init_db`)

| Variable | Sensitive | Meaning |
| --- | --- | --- |
| `DB_MASTER_USER`, `DB_MASTER_PASSWORD` | No, Secret | Privileged DB credentials, used only to create the app role. |
| `DB_APP_USER`, `DB_APP_PASSWORD` | No, Secret | Least-privilege role the app runs as. If unset, role creation is skipped. |
| `SEED_DB` | No | `true` to seed sample data in production (default off). |
| `SEED_ADMIN_PASSWORD`, `SEED_MANAGER_PASSWORD`, `SEED_DEV_PASSWORD` | Secret | **Required** to seed any non-`development` environment. Seeding fails closed without them. |
| `SKIP_INIT_DB` | No | `true` makes the worker entrypoint skip initialisation. |

> **Known limitation (Current):** the master connection to Postgres hardcodes `sslmode=require`. A database without TLS (typical local/on-prem) will fail in `init_db`. A configurable `DB_SSLMODE` is Planned.

### Secrets

Today secrets are read from plain environment variables only. Injecting them from a secret store (Kubernetes Secret, Vault agent, cloud secret manager) as env vars works everywhere. File-mounted secrets via `<NAME>_FILE` are **Planned**.

## 4. HTTP surface

Base path for the API: `/api/v1`. JSON in, JSON out. Authentication is `Authorization: Bearer <access token>`.

### Operational endpoints (unauthenticated, not rate limited)

| Path | Behaviour | Use for |
| --- | --- | --- |
| `GET /health` | `200` only if the database **and** Redis respond, else `503`. Body: `{status, database, redis, version, environment}` | Dependency health, monitoring, smoke tests |
| `GET /ready` | Always `200 {"ready": true}`. Does not check dependencies. | Current readiness probe |
| `GET /metrics` | Prometheus text format | Prometheus scrape |
| `GET /` | Service info | Sanity check |
| `GET /apidocs` | Swagger UI, only if `ENABLE_SWAGGER=true` | Humans |

**Probe guidance (important).** Do not use `/health` as a Kubernetes _liveness_ probe: it fails when Postgres or Redis is down, so a dependency outage would restart every pod and make recovery worse. Use a TCP or `/ready` check for liveness. Use `/health` for readiness/traffic gating and for external monitors. A dedicated `/live` and a dependency-aware `/ready` are **Planned**.

The Docker `HEALTHCHECK` in `Dockerfile` calls `healthcheck.py`, which requests `http://localhost:5000/health`. The `HEALTHCHECK` parameters in the `Dockerfile` are: interval 30 s, timeout 5 s, start period 60 s, 3 retries. Orchestrators that ignore `HEALTHCHECK` (Kubernetes, Nomad) must define their own probes.

### API resources (Current)

| Resource | Endpoints |
| --- | --- |
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me` |
| Users | `GET /users`, `GET/PUT/DELETE /users/{id}` |
| Teams | `GET/POST /teams`, `GET/PUT /teams/{id}` |
| Projects | `GET/POST /projects`, `GET/PUT /projects/{id}` |
| Tasks | `GET/POST /tasks`, `GET/PUT/DELETE /tasks/{id}` |
| Comments | `GET/POST /tasks/{id}/comments` |

Roles: `admin`, `manager`, `developer`. Every mutation writes to `audit_logs`.

### Rate limits

Defaults: 200/day and 50/hour per client, plus per-route limits (login 10/min, register 5/hour). Keys use the request's remote address.

### Reverse proxy / ingress requirements

- The app is served by gunicorn on plain HTTP, port `5000`. Terminate TLS at the proxy.
- **Client IP:** rate limiting and the audit log use `request.remote_addr`. The app does **not** yet trust forwarded headers, so behind a proxy every client currently appears as the proxy's IP, and rate limits become shared across all users. Forwarded-header support (`X-Forwarded-For/Proto/Host`) with a configurable trusted-proxy count is **Planned**; until then, put the proxy in a mode that preserves the source IP, or accept the limitation.
- Request timeout: gunicorn `--timeout 120`. Set proxy timeouts ≥ this.
- Set a request-body limit at the proxy. Attachment upload limits are **Planned**.
- CORS is enforced by the app via `CORS_ORIGINS`; do not add a permissive CORS layer at the proxy.

## 5. Container contract

| Item | Value |
| --- | --- |
| Base | `gcr.io/distroless/python3-debian12:nonroot` (no shell, no package manager, no curl) |
| User | uid/gid `65532` (non-root) |
| API port | `5000` |
| Entrypoint | The distroless Python interpreter. **A command override must start with `-m <module>` or a script path, never `python`.** |
| Default API command | `-m gunicorn --chdir src --bind 0.0.0.0:5000 --workers 2 --threads 2 --worker-class gthread --timeout 120 wsgi:app` |
| Default worker command | `/entrypoint_worker.py celery -A src.celery_worker:celery worker --loglevel=info --concurrency=2` |
| Beat command (worker image, command override) | `/entrypoint_worker.py celery -A src.celery_worker:celery beat --loglevel=info`. Verified: it starts in the built `Dockerfile.worker` image. |
| Beat schedule | None is defined today (`beat_schedule` is empty), so beat starts and runs no jobs. |
| Beat state file | `celerybeat-schedule.db`, written to the working directory `/app`. Verified in the built image: `/app` is owned by uid `65532` and writable. |
| Signals | `SIGTERM` for shutdown; see "Shutdown behaviour" below |
| Logs | stdout/stderr, plain text (JSON logs are **Planned**) |

The image `CMD` starts gunicorn with 2 workers, 2 threads per worker (`gthread` worker class) and a 120 s worker timeout. These are fixed in the `Dockerfile`; no environment variable changes them. Override the command to change them until env-driven tuning (**Planned**) lands.

**Read-only root filesystem.** Verified for beat: with `docker run --read-only` it exits with `OSError: [Errno 30] Read-only file system: 'celerybeat-schedule'`, because its state file goes to `/app`. Not verified (open items): api and worker on a read-only root filesystem, and whether beat's state file path can be moved. Assume `/tmp` writes for api and worker.

**Probes for worker and beat.** `Dockerfile.worker` declares no `EXPOSE` and no `HEALTHCHECK`, and neither process serves an HTTP endpoint. The images have no shell, so a probe can only be a command that runs an executable present in the image, or be omitted. Whether a particular command (for example `celery inspect ping`) is a reliable liveness signal has not been verified.

**Sizing.** CPU and memory needs of api, worker and beat have not been measured, and the images set no limits. This repo publishes no sizing figures.

### One-shot tasks

Run to completion, then exit. Override the container command:

| Task | Command | When |
| --- | --- | --- |
| Initialise | `-m src.init_db` | Before the first start and on every release. Idempotent. Creates the app DB role, applies migrations, optionally seeds. |
| Smoke test | `-m src.smoke_test` | After a deploy, against the new version. |
| Queue-depth check | `-m src.queue_depth_check` | Before cutting over blue/green: verifies the target worker is consuming and the old queue is drained. |

#### Inputs and exit codes

**`-m src.smoke_test`**

- Inputs: `SMOKE_TEST_TARGET_URL` (base URL of the API to check) and `SEED_ADMIN_PASSWORD` (password of the seeded `admin` user, which must exist).
- Checks, in order, with a 5 s timeout per request: `GET /health` must return 200; `POST /api/v1/auth/login` as `admin` must return 200 with an `access_token`.
- Exit `0` if both pass. Exit `1` if either input is unset, `/health` is not 200, the login fails, or the request errors or times out.
- Side effect: each successful login writes one `login` row to `audit_logs`, even though the module's own docstring calls the login read-only.

**`-m src.queue_depth_check`**

- Inputs: `CELERY_BROKER_URL` and `CELERY_TASK_QUEUE`. The queue is the one being checked, that is, the queue whose worker is being retired.
- Exit `0` only when the queue's Redis lists (the base list and every priority list) are empty **and** every worker consuming that queue answered the inspect request and reports no reserved or active task routed to it.
- Exit `1` if either input is unset, the broker cannot be queried, the queue is not empty, a task is in flight, or no worker consuming that queue replied to the inspect request. The last case fails closed ("cannot confirm"), so running this after that queue's worker has already stopped exits `1`.
- A worker finishing a task makes it exit `1` temporarily, so callers are expected to retry or poll.

**`-m src.init_db`**: inputs are in the "Database initialisation" table above.

### Shutdown behaviour

Facts from the installed library versions (gunicorn 26.2.0, Celery 5.6.3) and the code. Nothing here sets a termination deadline for you.

- **api (gunicorn).** `SIGTERM` to the gunicorn master starts a graceful stop: it closes its listening sockets, sends `SIGTERM` to the workers, waits up to `graceful_timeout`, then sends `SIGKILL` to any worker still running. `graceful_timeout` is gunicorn's default of 30 s; the `Dockerfile` does not set it. The 120 s `--timeout` is the worker's request timeout, a separate setting. `SIGINT` and `SIGQUIT` start a quick shutdown instead.
- **worker (Celery).** `entrypoint_worker.py` replaces itself with the Celery process (`os.execv`), so Celery is the container's main process and receives `SIGTERM` directly. `SIGTERM` starts Celery's warm shutdown (it sets the worker's `should_stop` state). Celery documents this as finishing the tasks it is currently executing before exiting; that was not exercised here. If the environment variable `REMAP_SIGTERM` is set to `SIGQUIT`, `SIGTERM` becomes a cold shutdown instead.
- **Acknowledgement.** The app leaves `task_acks_late` off (`False`) and the prefetch multiplier at Celery's default of 4. Messages are therefore acknowledged when a worker receives them, before the task runs, so a task that was received but not finished is not redelivered if the worker process is killed. The `queue_depth_check` module documents the same behaviour.
- **beat.** `SIGTERM` and `SIGINT` save the schedule and exit. Verified in the built image: exit code `0` after `SIGTERM`.

### Migrations

- Alembic via Flask-Migrate, in `migrations/`. Applied by `-m src.init_db`.
- On PostgreSQL the upgrade runs under a session advisory lock, so concurrent callers are safe: the second waits, then no-ops.
- **Current behaviour:** the worker entrypoint also runs `init_db` on start when `DB_MASTER_USER`/`DB_MASTER_PASSWORD` are present, unless `SKIP_INIT_DB=true`. A Kubernetes consumer (such as Kubeforge) should run a single pre-release Job and set `SKIP_INIT_DB=true` on workers and beat, so pods never need master DB credentials.
- Migrations are forward-only. Deploy schema changes compatible with the previous app version (expand, then contract) if you roll back or run blue/green.

## 6. Deployment recipes

### Kubernetes (consumer guidance)

This repo ships images only; the chart and cluster configuration are owned by [Kubeforge](https://github.com/harshit-kandhwey/Kubeforge). What this contract requires of any Kubernetes deployment:

- Three workloads: `api`, `worker`, `beat` (beat: `replicas: 1`, `strategy: Recreate`).
- A one-shot Job (for example a Helm pre-install/pre-upgrade hook) running `-m src.init_db`.
- Workers and beat: `SKIP_INIT_DB=true`.
- `api`: readiness `GET /health` on `5000`; liveness a TCP socket or `/ready`; a startup probe allowing ~60s.
- Run as uid `65532`, `runAsNonRoot`, drop all capabilities, `seccompProfile: RuntimeDefault`.
- A `PodDisruptionBudget` on `api` and `worker`, and a `preStop` sleep of a few seconds.
- Scale workers on Celery queue length (e.g. KEDA Redis scaler on the `CELERY_TASK_QUEUE` list).
- Ingress: TLS at the controller, proxy timeouts ≥ 120s, no CORS at the proxy.
- A change to this contract lands in this repo and in every consumer together.

### docker-compose / VM / on-prem

Run `api`, `worker`, `beat`, Postgres and Redis. The repo's root `docker-compose.yml` is a development file (bind-mounts the source, dev secrets) and does **not** yet run the schema initialiser; run `-m src.init_db` once before use. Hardened production compose definitions are the consumer's to supply; this repo keeps only the development file.

### Workers of different versions on one queue

A task is published under its name with its arguments, and any worker consuming the queue may run it. If workers of two app versions consume the same queue, a task published by one version can be executed by the other, which may not know that task or its argument shape. The app addresses this only through `CELERY_TASK_QUEUE` (each deployment publishes to, and its worker consumes, one named queue); nothing in the app prevents two versions from sharing a queue name. Any update mechanism that runs old and new workers at the same time carries this risk.

### Blue/green or canary

Give each slot its own `CELERY_TASK_QUEUE`. Each slot's worker is then the only consumer of its own queue, so a task published by version N is never executed by version N-1. Run `-m src.queue_depth_check` to confirm the target slot's worker is consuming and its queue is drained before switching traffic.

## 7. Observability

- **Metrics** (`/metrics`): `app_requests_total{method,endpoint,status}`, `app_request_latency_seconds{endpoint}`. Note: gunicorn runs multiple processes and multiprocess-mode Prometheus is **not** configured, so each scrape sees one worker's counters. Treat metric values as approximate until this is fixed (**Planned**).
- **Tracing:** set `OTEL_EXPORTER_OTLP_ENDPOINT` (OTLP/HTTP). Flask, SQLAlchemy, Redis and Celery are instrumented. It is identical for api, worker and beat.
- **Logs:** stdout. Passwords in DB/Redis URLs are redacted where the app logs them.
- **Audit trail:** the `audit_logs` table, including failed logins.

## 8. Security expectations of the platform

- Provide `SECRET_KEY`/`JWT_SECRET_KEY` from a secret store, unique per environment. Rotating `JWT_SECRET_KEY` invalidates all sessions.
- Give the app the **least-privilege** DB role (`DB_APP_USER`); keep master credentials out of long-running pods.
- Restrict network access: only the ingress reaches `api:5000`; only app pods reach Postgres and Redis. Redis must require AUTH/TLS outside a trusted network (use `rediss://`).
- Keep `/metrics` off the public internet.
- Leave `ENABLE_SWAGGER` off in production unless deliberately exposed.

## 9. Compatibility and change policy

- API is versioned under `/api/v1`. Breaking changes will go to `/api/v2`.
- Additions to this contract (new env vars, new endpoints) are backwards compatible; removals or renames are called out in release notes and here.
- Pin to an image tag or digest, not `latest`.
