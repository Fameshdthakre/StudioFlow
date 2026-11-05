# StudioFlow

> This is the archived SQLite-era guide. For the current PostgreSQL/SeaweedFS release, use the [root README](../README.md), [architecture](ARCHITECTURE.md), and [deployment runbook](DOCKER_DEPLOY.md). Do not use the legacy deployment steps below for the platform upgrade.

StudioFlow is a team workspace for creating, reviewing, revising, and approving product images. It combines FastAPI, a dependency-free JavaScript frontend, SQLite, and local image storage. Demo mode creates previews without paid requests; live mode edits uploaded photographs through the image provider.

## Run locally

Use Python 3.12 or newer. The current lock file and regression suite were verified on Windows with Python 3.13. Docker uses Python 3.12; verify its image build on a host with Docker installed.

For a new setup, copy `.env.example` to `.env` and configure `TEAM_USERS` with your own credentials. Keep an existing `.env` when upgrading. The first configured account is the owner unless `ADMIN_USERS` names owners explicitly.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open [StudioFlow](http://localhost:8000). Application directories and additive database migrations are initialized during startup, rather than module import.

For an explicitly anonymous local demo, override configuration for that shell and keep the server on loopback:

```powershell
$env:STUDIOFLOW_LOAD_DOTENV='0'
$env:APP_MODE='demo'
$env:TEAM_USERS=''
$env:STUDIOFLOW_REQUIRE_AUTH='0'
$env:ALLOW_ANONYMOUS_DEMO='1'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Anonymous requests require both a loopback client address and a localhost host name. Containers always require accounts. See [DOCKER_DEPLOY.md](DOCKER_DEPLOY.md) for deployment, backups, permissions, and upgrades.

## Architecture

| Component | Responsibility |
| --- | --- |
| `app.py` | HTTP routes, authentication, catalog and memory workflows, prompt assembly, application lifecycle |
| `studioflow/settings.py` | Validated environment configuration |
| `studioflow/database.py` | SQLite connections, rollback handling, additive versioned migrations |
| `studioflow/provider.py` | Model capabilities, source-image editing, uncropped output resizing |
| `studioflow/jobs.py` | Persistent task claims, worker heartbeats, shutdown and interrupted-task recovery |
| `studioflow/submissions.py` | Transactional request fingerprints and idempotency replay |
| `static/` | Browser state, views, API requests, upload and review controls |
| `data/` | Runtime SQLite database, source uploads, output images, brand logos |

The queue claims persisted renders from SQLite. Queued tasks survive a restart. Work already sent to a paid provider has an uncertain outcome after a crash, so abandoned running tasks fail for review after the 90-second worker lease expires; they are never automatically resubmitted. Graceful shutdown allows active renders to finish. Each process has its own `MAX_WORKERS` concurrency, so keep total process count in your billing and capacity budget.

Catalog identifiers resolve to a canonical SKU. Ambiguous ASINs or item codes require an exact SKU. Catalog-derived facts and manually verified facts are stored separately, and each render retains its prompt-memory snapshot. Permanent revision corrections become active when the resulting image is approved.

Supported model and quality choices come from the backend configuration, keeping the browser and provider adapter consistent. The adapter makes source-image edits, avoids hidden paid SDK retries, and pads exports instead of cropping the product to fit a different aspect ratio.

## Configuration

| Variable | Default / behavior |
| --- | --- |
| `APP_MODE` | `demo`; use `live` to enable paid image requests |
| `TEAM_USERS` | Comma-separated `username:password` accounts; required except for an explicit local anonymous demo |
| `ADMIN_USERS` | Comma-separated owner usernames; defaults to the first account |
| `OPENAI_API_KEY` | Required in live mode |
| `MAX_WORKERS` | `3`, clamped to 1–8 per application process |
| `COOKIE_SECURE` | `0` for local HTTP; set `1` for HTTPS |
| `ALLOW_ANONYMOUS_DEMO` | `0`; explicit local-only opt-in |
| `STUDIOFLOW_DATA_DIR` | Repository `data/` locally; `/app/data` in Compose |
| `STUDIOFLOW_LOAD_DOTENV` | `1` locally; Compose injects variables and sets `0` |
| `STUDIOFLOW_REQUIRE_AUTH` | Compose forces `1` regardless of `.env` |
| `STUDIOFLOW_BIND_ADDRESS` | Compose-only host binding; defaults to `127.0.0.1` |

Keep `.env` and `data/` private. They are excluded from Docker builds and source control. Team passwords are currently configured as secrets in the environment; use separate credentials for this application and HTTPS for remote access.

## Verify changes

The locked dependencies include the HTTP client used by FastAPI's test client. For flexible dependency development, install `requirements-dev.txt` instead and rerun the suite before refreshing the lock file.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/frontend.test.cjs
```

On Linux/macOS, use `.venv/bin/python` for the Python command. Tests use temporary databases and mocked provider calls; they do not access the workspace database or make paid image requests.
