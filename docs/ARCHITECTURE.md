# StudioFlow architecture

StudioFlow is one FastAPI application with feature services, PostgreSQL records, private object storage and a plain JavaScript interface. Workers run inside the application process; their leases and queues live in PostgreSQL. Infrastructure has its own persistent Docker project, separate from application replacement.

```text
app.py                         compatible Uvicorn entry point
studioflow/
  application.py               app factory, request maintenance gate, worker lifecycle
  settings.py, workspace.py     explicit settings, paths, database/storage boundaries
  api/                         request schemas, authentication, streaming responses
  services/
    catalog.py, memory.py       catalog facts, brand profiles, approved rules
    rendering.py, creation.py   legacy and versioned mixed submissions, revisions
    pipeline.py                paid receipts, masters, composition and publication
    assets.py                  immutable reservations, transfers, provenance
    library.py                 indexed queries, slots, export manifests and ZIP jobs
    auth.py, aplus.py, usage.py existing team workflows and accounting
    maintenance.py             writer gates and operational state
  persistence/
    schema.py                  SQLAlchemy Core schema and foreign keys
    connection.py              pooled PostgreSQL adapter and transaction boundary
    repository.py              stable identities, sequencing and asset references
    alembic/                   explicit ordered schema migrations
  operations/                  audit, import, maintenance, backup, restore, cleanup
  jobs.py                      ordered claims, dependencies, leases, recovery
  composition.py               versioned local layout renderer
  provider.py, pricing.py      model capabilities, provider boundary, versioned rates
  database.py, migrations/     legacy SQLite source and isolated preview support
static/
  js/create_v2.js, layout.js    mixed creation and controlled graphic copy
  js/gallery.js                cursor pages, durable selection, downloads, local revisions
  js/                          existing feature controllers and shared API helpers
  css/, index.html             shipped browser UI (no application bundler)
resources/fonts/               licensed Cinzel/Roboto files and license notices
deploy/                        persistent infrastructure, application and operator Compose
scripts/                       guarded release packaging, maintenance and smoke checks
tests/                         offline, real-service, scale and browser acceptance
```

## Persistence boundaries

Routers validate public requests and resolve authenticated users. Services own transactions and snapshots; identity/asset repositories sit below them. `Workspace` supplies the database and storage boundary. SQLAlchemy Core owns the schema and PostgreSQL engine; a small adapter preserves existing parameterized feature queries. It converts placeholders and quotes the legacy `user` column. Dialect-specific behaviour stays explicit.

Stable product IDs link catalog rows, memory, rules, renders and image sets. Historical public IDs, usernames, session tokens, submission keys and usage ledger IDs survive migration. Catalog deletion archives the row. Saved SKU/ASIN identifiers prevent catalog edits from renaming existing exports. Approval events supplement retained legacy approval flags.

Image sets snapshot inputs, model/quality/size, facts and branding. Each render stores its own immutable recipe and references. Paid attempts are counted separately from local outputs. Receipts preserve the rate card and provider request ID used at submission. Integer microdollar totals and ledger checkpoint boundaries are retained.

Assets have immutable keys, SHA-256 checksums, dimensions, MIME types, roles, readiness state and provenance links. `legacy_assets` maps historical URLs. New uploads retain original bytes and a normalized RGBA reference; cutouts preserve transparency. The storage adapter only exposes immutable operations. SeaweedFS services have no public host ports.

## Processing and recovery

Submission expansion and capacity checks share a transaction with deduplication. Limits are 50 products, 12 outputs per product, 250 outputs per job, 18 MiB per upload and 100 MiB of selected references, plus the existing pending-queue cap. A product set has at most one MAIN. PT slots follow submission order; local and paid revisions inherit them and start unapproved.

Workers claim `queued` or due `transferring` rows in `enqueue_seq` order with `FOR UPDATE SKIP LOCKED`. A composition dependency must be completed first. Heartbeats run independently of render and export work. An auxiliary worker resumes upload transfers and prepares ZIPs. Stale local work or a saved response can resume; an uncertain paid call becomes a failed item requiring review.

The paid boundary is: persist attempt → call once with SDK retries disabled → fsync response receipt → persist metering → save immutable master → compose/resize → transfer output and thumbnail → publish completed status. Raw masters and receipts survive output/transfer failures. A mismatched requested native resolution retains the master for review and fails publication. Later layout editing uses the master and creates no provider attempt.

Local MAIN arranges an approved single-item cutout using the confirmed pack count. Infographics apply exact copy and geometry with bundled fonts. Text that cannot fit is rejected rather than truncated. Recipes carry their version and confirmed facts. Source captions are never treated as product measurements.

## Query and export boundaries

`GET /api/library` uses indexed descending `enqueue_seq` cursors and server filters. `GET /api/renders` remains a compatible bounded legacy endpoint. Original media URLs and authentication remain available.

Selection stays in the browser across page/filter refreshes. Export preview resolves stable names and blocks case-insensitive collisions. The server freezes an authenticated manifest, checks 250-image/512-MiB limits, verifies each object checksum, and builds a ZIP one image at a time. ZIPs are temporary; restored databases regenerate unexpired exports. Individual listing downloads share the filename resolver.

## Operations and deployment

All API writers enter a shared PostgreSQL maintenance gate. Backups set maintenance, acquire the exclusive gate, and drain active operations before `pg_dump`. They capture an asset manifest and pending transfer/receipt files, resume service, then stream immutable objects to Restic. An operation lock excludes import/backup/cleanup conflicts. Linked assets are retained; cleanup removes expired ZIPs, old completed staging files and verified abandoned uploads only.

Production startup verifies the Alembic revision without applying it. The first migration is an explicit maintenance workflow. Regular deployment validates schema/storage/readiness before stopping the running application. An external restore proof and reconciliation marker are required for activation. Application replacement never replaces PostgreSQL or SeaweedFS volumes.

Use [the deployment runbook](DOCKER_DEPLOY.md) for the exact sequence. Restore only into an empty database and bucket. Once new writes begin, recovery must preserve/reconcile them; no automated SQLite rollback exists.
