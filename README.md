# StudioFlow

StudioFlow turns original product photos into reviewed MAIN, lifestyle, infographic and A+ backgrounds. The current app keeps its shared catalog, product memory, persistent sessions and durable generation queue while adding the selected reference-app features.

## Run locally

Use Python 3.12 or 3.13. In PowerShell, from this directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
```

For a new installation, copy `.env.example` to `.env` and configure `TEAM_USERS`. Keep an existing `.env`; it contains your settings and credentials. `ADMIN_USERS` selects workspace owners; if blank, the first configured account is the owner.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. `APP_MODE=demo` creates local previews without provider calls. Live generation requires `APP_MODE=live`, an API key and an account with access to the selected model. Account model access has not been tested with paid requests. For a standalone local demo without accounts, explicitly set `ALLOW_ANONYMOUS_DEMO=1`; this mode accepts only loopback requests. Containers always require team accounts.

Do not point a demo preview at a live workspace's data directory: startup resumes its queued work. Use a separate `STUDIOFLOW_DATA_DIR` and `STUDIOFLOW_LOAD_DOTENV=0` for isolated previews.

## Integrated workflows

- **A+ Content Studio:** owners save independent instructions, fonts, colors and logo references. Teams select a profile, upload product photos, review matched SKUs, enter scenes and choose desktop 1464 × 600 or mobile 600 × 450 output. Each accepted job saves the entire profile, so later profile changes do not alter that job or its revisions. Catalog facts and approved SKU corrections still apply. Add final text and logo artwork in a design tool.
- **Six image models:** Flare and Sunburst remain the generation and revision defaults. GPT Image 2, GPT Image 1.5, GPT Image 1 Mini and ChatGPT Image Latest are also available. The API and all three model selectors share the same quality capabilities; `xhigh` and `max` are restricted to the two 2.5 models. Existing general-generation API resolutions are preserved. A+ uses closer aspect ratios on flexible-size models. Final JPEG exports use padding to retain the complete image.
- **Usage & credits:** live provider attempts are recorded before the request, then raw usage and the request ID are saved immediately after the response, before decoding or exporting the image. A failed export can therefore retain its usage receipt. A timeout without a response has an unknown cost. Demo previews do not count as paid requests.

The usage dashboard estimates StudioFlow spending only. It is not an invoice or an account balance API. A manually checked balance checkpoint subtracts subsequent known usage; missing or partial usage remains visibly unknown. It does not add credits, enforce a hard spending cap, or include other applications, taxes or historical requests that were never recorded. Owners must wait for active requests to finish and check OpenAI Billing before saving a checkpoint.

Rate cards are versioned and saved on each attempt. Prices use integer microdollars, with distinct image/text output rates where published. The Standard synchronous Images API rates were checked on **2026-11-02** against [OpenAI pricing](https://developers.openai.com/api/docs/pricing). GPT Image 2/2.5 cached-input discounts listed for the Responses API are not applied to this Images API workflow. When prices change, update the version and rates in `studioflow/pricing.py`; old receipts retain their original estimates.

## Architecture

The application is organized by feature, with an application factory, API routers, services and browser ES modules. See [the source structure and extension guide](docs/ARCHITECTURE.md).

| Component | Responsibility |
| --- | --- |
| `app.py`, `studioflow/application.py` | Compatible Uvicorn entry point, application factory and worker lifecycle |
| `studioflow/api/` | Request schemas, authentication dependencies, feature routes and HTTP responses |
| `studioflow/services/` | Catalog, memory, render execution, A+ profiles and usage transactions |
| `studioflow/workspace.py`, `dependencies.py` | Per-application paths, connections and request authentication |
| `studioflow/settings.py` | Runtime configuration and explicit demo/authentication rules |
| `studioflow/database.py`, `migrations/` | SQLite connections and the existing ordered migrations 1–4 |
| `studioflow/jobs.py` | Atomic task claiming, worker heartbeats and recovery |
| `studioflow/submissions.py` | Transactional request deduplication |
| `studioflow/provider.py` | Model capabilities, image-edit requests and atomic padded exports |
| `studioflow/pricing.py` | Versioned provider rates and usage estimates |
| `static/js/`, `static/css/` | Browser bootstrap, shared helpers, isolated screen controllers and styles |
| `resources/` | Downloadable catalog CSV template |
| `deploy/`, `scripts/`, `.github/workflows/` | Production Compose configuration, release tooling and CI/CD |

Jobs and renders are committed with their submission keys. Browser retries reuse the same key after a lost response; changed submissions use a new key. Queued work survives restarts. A running request whose worker disappears is marked failed rather than retried automatically, because its provider-side result and charges may be unknown. The SDK also has automatic retries disabled. Revision corrections become permanent product rules only after approval.

SQLite and its uploads, outputs and logos form one workspace. Persist and back up the whole data directory. The current design targets a shared, single-host team workspace; `MAX_WORKERS` controls provider concurrency per process. Use one application process unless intentionally managing the aggregate provider concurrency.

Once committed and enabled, pull requests run tests and a Docker smoke check on GitHub-hosted runners. Pushes to `main` run the same checks, package the tested image and deploy it on `mvserver`, using `/srv/studioflow` for persistent state. Configure the runner label and production secret described in [the deployment guide](deploy/README.md) before activating the workflow.

## Feature controls and upgrades

Both new views are enabled by default:

```dotenv
STUDIOFLOW_ENABLE_APLUS=1
STUDIOFLOW_ENABLE_USAGE=1
```

Set a flag to `0` and restart to hide its view and disable its API. The A+ flag rejects new A+ submissions and revisions; it does not cancel already accepted work. Disabling the usage dashboard does not disable live-request receipts. Schema migrations still run with either flag disabled.

Migrations 3 and 4 add A+ profiles, `renders.aplus_profile_id`, the usage ledger and the checkpoint table. Existing catalog, memory, sessions and history remain in place. These migrations extend the current app's database; do not replace it with the reference app's database or source tree. The source reorganization preserves those migration versions and adds no schema change. See [the deployment guide](deploy/README.md) for backup, rollout and recovery instructions.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

Use Node 24 for the browser tests, matching CI. No npm install or frontend build step is required for the application or its unit tests.

Tests create independent app instances with temporary databases, demo rendering and mocked provider responses. They cover catalog/authentication/queue flows, A+ snapshots and revisions, model guards, usage after failed exports, unknown costs, checkpoint boundaries, migration preservation, bundled module paths and templates, duplicate submissions and delayed browser responses. They do not make paid API calls.
