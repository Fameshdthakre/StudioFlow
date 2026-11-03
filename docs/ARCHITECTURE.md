# StudioFlow architecture

The source reorganization is implemented. StudioFlow remains one FastAPI application with a shared SQLite workspace and a plain JavaScript browser interface. Start it with the existing `uvicorn app:app` command; the API URLs and database schema are unchanged.

```text
StudioFlow/
├── app.py                         # Uvicorn compatibility entry point
├── studioflow/
│   ├── application.py             # create_app(settings), lifespan, routers
│   ├── workspace.py               # Per-app settings, paths and connection factory
│   ├── dependencies.py            # Request workspace and authenticated user
│   ├── settings.py                # Environment validation and submission limits
│   ├── database.py                # Connections and ordered migration runner
│   ├── migrations/
│   │   ├── base.py                # Original schema
│   │   ├── v001_workspace.py
│   │   ├── v002_catalog_uniqueness.py
│   │   ├── v003_aplus.py
│   │   └── v004_usage.py
│   ├── api/                       # Validation schemas and HTTP endpoints
│   │   ├── auth.py
│   │   ├── system.py
│   │   ├── catalog.py
│   │   ├── memory.py
│   │   ├── renders.py
│   │   ├── aplus.py
│   │   └── usage.py
│   ├── services/                  # Feature operations and transactions
│   │   ├── auth.py
│   │   ├── system.py
│   │   ├── common.py              # Audit records and timestamps
│   │   ├── catalog.py
│   │   ├── memory.py
│   │   ├── rendering.py
│   │   ├── aplus.py
│   │   └── usage.py
│   ├── provider.py                # Image API adapter and model capabilities
│   ├── pricing.py                 # Versioned rates and metering calculations
│   ├── jobs.py                    # Durable queue, leases and recovery
│   └── submissions.py             # Transactional submission deduplication
├── static/
│   ├── index.html
│   ├── css/styles.css
│   └── js/
│       ├── app.js                 # Composition, navigation, login and polling
│       ├── api.js                 # HTTP errors and per-workspace request keys
│       ├── dom.js                 # DOM lookup, escaping and toasts
│       ├── models.js              # Model/quality selectors
│       ├── create.js
│       ├── catalog.js
│       ├── jobs.js
│       ├── gallery.js
│       ├── memory.js
│       ├── aplus.js
│       └── usage.js
├── resources/studioflow_product_template.csv
├── tests/                         # Temporary workspaces and offline regressions
├── deploy/                        # Production Compose and operator guide
├── scripts/                       # Container smoke check, packaging, deployment
├── .github/workflows/             # CI and mvserver deployment
├── docs/
├── Dockerfile
├── docker-compose.yml             # Local/manual Compose
└── requirements*.txt / requirements.lock
```

## Backend boundaries

`application.create_app(settings)` constructs an app without creating directories, opening a database or starting workers. Its lifespan creates the workspace directories, initializes the database and starts a queue bound to that workspace. Shutdown drains that queue. Passing explicit settings avoids loading `.env`; omitting settings uses the normal environment configuration.

`Workspace` carries each app's settings, data paths and connection factory. Routers obtain it from `request.app.state` through `get_workspace`; authentication uses the same workspace. Tests use `create_app(settings, start_queue=False)` to inspect queued work without running it, or enable the queue for complete demo workflows. No test needs to dynamically import or patch a second copy of `app.py`.

Feature routers own Pydantic request schemas, dependencies, file responses and route declarations. Services own identity resolution, prompt composition, snapshots, persistence and transaction boundaries. Existing service validation continues to raise the same HTTP errors to preserve API behavior. Services never import the application or routers. Shared queue, provider, pricing and submission adapters remain separate from feature routes.

The migration runner applies versions 1–4 in their original order and transaction. Version 2 still defers its unique index when legacy duplicate SKUs exist, preserves those records, and retries on a later startup. The refactor adds no migration version and does not rename tables, paths, columns or stored records.

## Browser boundaries

The page loads one ES module, `static/js/app.js`. It creates one shared API client, configuration object and submission-key registry, then injects them into screen controllers. Each controller keeps its own edits, selected records and request counters inside its factory. Cross-screen actions are explicit callbacks supplied by the bootstrap.

`api.js` retains submission keys after uncertain responses and clears them after success. `models.js` uses the backend's model capabilities. Screen code retains request snapshots, duplicate-click guards, protection against stale responses and unsaved profile/checkpoint edits. Dynamic buttons use event listeners, so they do not require global browser functions.

The browser serves these modules directly. `static/js/package.json` declares ES-module syntax for Node's tests; no bundler, npm dependencies or build step is needed. The frontend tests import the same modules as the browser and exercise their controls with isolated DOM and API fixtures.

## Adding a feature

Add its request schema and router under `api/`, its transactions under `services/`, and register the router in `application.py`. Reuse the shared workspace/auth dependencies. Keep submission deduplication and prompt snapshots in the same transaction as new render records. Add a numbered migration only when the database actually needs a change.

For a new browser view, add a screen factory and bind it in `static/js/app.js`. Inject shared helpers and callbacks rather than accessing another screen's internal state. Update the HTML, then run the Python and browser suites listed in the root README. CI additionally builds the image and runs a disposable container smoke check.

## Production state

`/srv/studioflow` holds private runtime configuration, data, backups and release metadata on `mvserver`. It stays outside the repository and Actions checkout. The production Docker image includes only application sources and shipped resources. See [deployment and recovery](../deploy/README.md).
