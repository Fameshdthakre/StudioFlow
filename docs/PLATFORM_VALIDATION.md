# Platform upgrade validation and remaining gates

Implementation branch: `codex/studioflow-platform-upgrade`. Validation date: 2026-11-05. All image-provider responses used in automated checks are offline fixtures; no paid generation was performed.

## Implemented

| Area | Delivered |
| --- | --- |
| Relational foundation | PostgreSQL 18, SQLAlchemy Core schema/repositories, Alembic, stable identities, relationships and preserved legacy public fields |
| Private images | SeaweedFS S3 boundary, originals/references/cutouts/masters/outputs/thumbnails/logos, immutable keys, checksums, authenticated delivery and legacy mappings |
| Reliable work | Ordered claims and dependencies, dedicated lease renewal, durable receipts/transfers, local recomposition and no automatic repeat of uncertain paid calls |
| Quality controls | High default, validated native-size gate, actual master dimensions, five local templates with licensed fonts, checked copy fit, confirmed facts and unapproved revisions |
| Creation | Grouped product/style references, independent image-type selections, retained settings, output/request breakdown, expanded limits and complete submission fingerprints |
| Library/export | Cursor pages, thumbnails, persistent selection, stable MAIN/PT slots, ASIN/SKU snapshots, collisions, individual downloads and authenticated bounded ZIPs |
| Operations | Read-only audit, frozen snapshots, resumable import/reconciliation, daily encrypted backup scheduler, conservative cleanup and empty-stack restore verification |
| Release safety | Separate persistent infrastructure, pinned images, expanded CI, pre-stop compatibility/restore guard and explicit first-maintenance workflow |

## Evidence

- The final full Python suite against real PostgreSQL/SeaweedFS ran **114 tests: 110 passed, four Linux-only directory-descriptor checks skipped**. It includes application compatibility, queue/lease recovery, provider contracts, deployment guards, all five compositions, migration, encrypted restoration and scale. A dedicated regression verifies that stalled queue housekeeping cannot stop worker lease renewal.
- Actual PostgreSQL **18.6**, SeaweedFS **4.52** and Restic **0.19.1** were exercised locally, using disposable databases/buckets. Migration preserves IDs, sessions, accounting and checksums; repeated imports reconcile.
- An encrypted **local Restic repository** restored into a fresh PostgreSQL database and a new private bucket. The restore verified bytes/counts/accounting, restored login, resumed a pending transfer and retained an uncertain paid attempt without resending it. This is not external S3 restore proof and cannot activate production.
- A **100,000 asset/render metadata record** test passed with 20 concurrent library readers and indexed cursor pagination. This does not simulate 100,000 full-resolution objects, 20 concurrent image uploads or paid-provider capacity; no production performance SLA is inferred.
- **21 frontend logic tests** pass. Headless Edge acceptance against the real isolated PostgreSQL/SeaweedFS app passed mixed creation, preserved deselected settings, high default, ZIP naming, selection across refresh/filter changes, local recomposition, mobile width and absence of browser errors.
- All four Compose configurations validate with Compose 2.40.3. Actions files pass actionlint 1.7.7. Python compilation, Ruff checks and Git whitespace checks pass. The actual Docker image build/container smoke checks require CI: this workstation has no Docker daemon.
- A read-only audit of the local legacy workspace found no conflicts: six catalog products, eight memory records, one job, six renders, six usage entries, three sessions and 9,380,302 referenced image bytes. This is the local copy, not an audit of mvserver production.

Reproducible test commands and environment variables are in the [root README](../README.md); tests do not need production credentials. Local logs and screenshots are under ignored `.tmp/` paths and are not part of the release artifact.

## SQLite preview and model follow-up (2026-11-05)

- Existing preview databases now receive the missing `assets.transfer_worker_id` column before background processing starts. The repeatable additive upgrade preserves saved assets and resumes interrupted transfers.
- A failed auxiliary task is consumed and logged once, allowing a later transfer/export pass to recover instead of repeatedly raising the same exception.
- All eight undated models from the requested list are available to creation, revisions and A+. Added `gpt-image-2.6-flare` and `gpt-image-2.6-sunburst`, with versioned labels, documented quality choices and usage rates. Dated snapshots remain excluded. Defaults are unchanged; 2.6 custom resolutions stay disabled pending separate validation. Sources: [official image guide](https://developers.openai.com/api/docs/guides/image-generation#gpt-image-26), [pricing](https://developers.openai.com/api/docs/pricing).
- The follow-up offline Python run completed **120 tests: 113 passed, seven skipped** (four Linux-specific checks, two PostgreSQL operation checks and the opt-in scale test). All **21 frontend tests** and lint checks passed. New regression coverage exercises a database missing the transfer column, repeated startup, preserved image bytes, auxiliary recovery, the shared model list and provider/accounting contracts without paid requests.

## Required before production activation

1. Green CI on the final committed revision, including Linux Docker builds, real-service integration, browser acceptance and container smoke checks.
2. A frozen copy of mvserver data; audit, capacity review and migration rehearsal with all conflicts resolved.
3. External S3 configuration and independently retained Restic recovery password, followed by a verified restore of the final imported state into an empty stack.
4. Authorized paid product benchmark and visual review. The implementation supports the requested workflow; comparable photographic quality has not yet been demonstrated on the real product. Native 2K stays disabled until model validation passes.
5. Agreed maintenance timing, explicit migration/activation/resume, and staged feature enablement using the [runbook](DOCKER_DEPLOY.md).

The existing local workspace has not been imported into PostgreSQL, and no production deployment/cutover has been performed. No commit or push was made. Production writes must remain on the existing stack until the maintenance gates are complete; subsequent recovery must preserve or reconcile any new writes.
