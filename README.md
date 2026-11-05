# StudioFlow

StudioFlow is a shared workspace for creating, reviewing and exporting product images. The platform upgrade uses PostgreSQL 18 for records and private SeaweedFS S3 storage for images. The browser remains plain JavaScript; the application is FastAPI with background workers.

The upgrade is developed on `codex/studioflow-platform-upgrade`. Existing production must complete the explicit migration and restore gates before this release can deploy. A push cannot automatically convert the old SQLite workspace.

## Run the isolated Docker stack

Keep an existing `.env` and `data/` directory intact. Copy `.env.platform.example` to `.env.platform`, set private team/database/storage credentials, and keep `APP_MODE=demo` for this first isolated run. `DATABASE_URL` must use the same password as `POSTGRES_PASSWORD`; URL-encode reserved characters in the URL.

```sh
docker compose --env-file .env.platform -p studioflow-local config --quiet
docker compose --env-file .env.platform -p studioflow-local build studioflow ops
docker compose --env-file .env.platform -p studioflow-local up -d --wait postgres seaweedfs
docker compose --env-file .env.platform -p studioflow-local run --rm ops init
docker compose --env-file .env.platform -p studioflow-local up -d --wait studioflow
```

Open [the isolated app](http://127.0.0.1:8001). Its named volumes and network are separate from an existing app on port 8000. PostgreSQL and SeaweedFS have no published host ports. `ops init` is repeatable and applies the checked-in Alembic migration; ordinary application startup only checks schema compatibility.

For production setup, migration, encrypted backups and restore commands, follow [the deployment runbook](docs/DOCKER_DEPLOY.md). Do not use `down -v` on a stack whose data must be retained.

## Creation and image quality

- Select MAIN, Lifestyle and Infographic independently. Deselecting a type retains its settings. Add extra product angles and separate style references under each product.
- New submissions default to high quality. MAIN uses a verified transparent single-item cutout when supplied, with the confirmed pack quantity. Without a cutout, MAIN requires a paid scene request in live mode.
- Five versioned graphic recipes cover Feature, Dimensions, Pack Contents, Usage/Styling and Detail Collage. Local rendering uses bundled licensed Cinzel and Roboto fonts, exact copy, dimension arrows, themes and colours. Product facts must be confirmed.
- Masters are saved before local composition. Editing a card layout creates an unapproved revision without another provider call. A provider image edit is a separate paid operation.
- Square final images are JPEGs at 2000 × 2000. Native 2048 × 2048 generation is available only for compatible models explicitly listed in `STUDIOFLOW_VALIDATED_2K_MODELS` after provider validation. Unsupported requests fail instead of silently changing size. The library displays actual saved master dimensions.
- Demo images are workflow fixtures. They do not establish photographic quality. A paid sample with the real product, approved cutout and confirmed facts remains necessary before accepting visual quality.

Existing model choices, catalog matching, product memory, A+ background creation, approvals, sessions, history and usage reporting are retained. A+ remains a background workflow; its desktop/mobile canvas sizes are preserved. Square card templates apply to Infographic outputs.

## Library and exports

The library uses server-side filters and cursor pagination, with private thumbnails. Selection persists across pagination, refresh and filters; hidden selections are counted. Select a page or all filtered completed images within the export limit.

Downloads use immutable image-set identifiers and slots: `ASIN.MAIN.jpg`, `ASIN.PT01.jpg`, or SKU equivalents. Auto naming prefers a valid ASIN; explicit ASIN/SKU modes and filename previews are available. Revisions keep their slot. Conflicting filenames are blocked until the selection or naming mode changes. Imported historical images require a reviewed slot assignment before listing-format export.

Authenticated server ZIPs are limited to 250 images / 512 MiB and expire after one hour. Individual downloads use the same naming rules. Legacy media URLs continue to resolve through asset mappings.

## Data and recovery

PostgreSQL holds stable product IDs, foreign keys, immutable generation specifications, layout recipes, asset metadata and usage receipts. Image bytes live in immutable private object keys. Originals, references, cutouts, masters, finished images, thumbnails and logos are separate assets. Application routes enforce authentication when serving them.

Workers claim ordered work using PostgreSQL `FOR UPDATE SKIP LOCKED` and leases. Durable response receipts allow transfers and local processing to resume. An uncertain paid request is never sent again automatically. Requests and balance checkpoints retain transaction fences and idempotency protections.

Backups pause writers, drain active work, capture a database dump and pending transfers, then resume service while immutable objects stream to encrypted Restic storage. The schedule is 03:00 Asia/Kolkata; retention is seven daily, four weekly and three monthly snapshots. An external S3 repository and a verified fresh-stack restore are required for production activation. See [architecture](docs/ARCHITECTURE.md) and [rollout evidence](docs/PLATFORM_VALIDATION.md).

## Feature controls

```dotenv
STUDIOFLOW_ENABLE_CREATION=1
STUDIOFLOW_ENABLE_COMPOSITION=1
STUDIOFLOW_ENABLE_EXPORTS=1
STUDIOFLOW_ENABLE_APLUS=1
STUDIOFLOW_ENABLE_USAGE=1
STUDIOFLOW_VALIDATED_2K_MODELS=
```

For initial production migration, start the first three flags at `0`, then enable exports, mixed creation and finished cards in stages. Flags reject new feature submissions but preserve accepted jobs and image access. Usage receipts continue even when the usage dashboard is hidden. Set an API key and `APP_MODE=live` only for an authorized live workspace; changing mode does not create a separate queue.

Usage totals are estimates of StudioFlow activity, with unknown costs shown explicitly. They are not the provider account balance, invoice or a hard spending cap. Versioned rate cards are stored on each attempt; local derivative images do not create extra paid attempts.

## Development and verification

Use Python 3.12/3.13 and Node 24. Install Python dependencies with `python -m pip install -r requirements.lock`.

```sh
python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

The default tests use temporary SQLite compatibility workspaces and mocked image responses. Set `STUDIOFLOW_TEST_DATABASE_URL` to a **disposable PostgreSQL server with database-creation privileges** and `STUDIOFLOW_TEST_S3_ENDPOINT` to disposable SeaweedFS to exercise the real stack. Tests create/drop isolated databases and buckets with `test-access` / `test-secret-long-enough` credentials. Set `STUDIOFLOW_RESTIC`, `STUDIOFLOW_PG_DUMP`, `STUDIOFLOW_PG_RESTORE` to the tool paths for backup tests; `STUDIOFLOW_SCALE_TEST=1` enables the 100,000-record test.

Browser acceptance tools are isolated in `tests/browser` (`npm ci`, `npx playwright install chromium`). Start `python -m scripts.browser_server` with the same disposable-service variables, then run `node tests/browser/acceptance.cjs`. Locally, `STUDIOFLOW_BROWSER_CHANNEL=msedge` can use installed Edge. These tests make no paid API requests. CI runs unit tests, real PostgreSQL/SeaweedFS integration, encrypted restoration, the scale test, browser acceptance and container smoke tests before packaging both images.

SQLite/local storage remains available for disposable previews and legacy import. For a preview, explicitly set `STUDIOFLOW_LOAD_DOTENV=0`, `STUDIOFLOW_DATA_DIR` to a new directory, `APP_MODE=demo`, and private `TEAM_USERS`; launch `uvicorn app:app --host 127.0.0.1 --port 8002`. Never point a preview at a live workspace: startup resumes queued work.
