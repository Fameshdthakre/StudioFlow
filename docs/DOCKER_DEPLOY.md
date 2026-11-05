# StudioFlow platform deployment and recovery

This release requires PostgreSQL 18 and private SeaweedFS. SQLite is the migration source and an offline preview option. **Normal deployment never imports production data or applies schema migrations.** Its preflight must succeed before the running application is stopped.

## Persistent layout

```text
/srv/studioflow/              runner-owned, mode 0700, outside the Actions checkout
  .env                       private last-successful runtime settings
  .deploy.lock               serializes deployment and manual maintenance
  current.json               last successful revision/image
  postgres/                  PostgreSQL-owned cluster data
  objects/                   SeaweedFS-owned data, UID/GID 10002
  data/                      application staging/receipts/exports, UID/GID 10001
  secrets/restic-password    private recovery password, readable by UID 10001
  migration/                 frozen import source and restore-verification.json
  backups/                   local stopped-workspace diagnostic archives
  releases/                  tested manifests, Compose and private configuration
  platform-releases/         explicit maintenance artifacts and configuration
```

The infrastructure project owns the PostgreSQL and SeaweedFS services. The application project replaces only `studioflow`. The operations project runs the daily backup scheduler. Keep all three on `STUDIOFLOW_NETWORK=studioflow-platform`. Database, S3, filer and volume ports are not published. The application defaults to host loopback port 8000; retain the existing HTTPS reverse proxy.

## Required configuration

Use `.env.platform.example` as the starting point for the complete GitHub `PROD_ENV_FILE` secret in both `production` and `production-maintenance` environments. Restrict those environments to `main`. Set:

- Existing `TEAM_USERS`, `ADMIN_USERS`, API key, intended `APP_MODE` and existing A+/usage settings. Preserve the original production mode while queued jobs exist. Use `COOKIE_SECURE=1` behind HTTPS.
- Private `POSTGRES_PASSWORD`, matching `DATABASE_URL=postgresql+psycopg://studioflow:URL_ENCODED_PASSWORD@postgres:5432/studioflow`.
- `STUDIOFLOW_STORAGE=s3`, `S3_ENDPOINT=http://seaweedfs:8333`, bucket, region and separate private primary-storage credentials.
- `STUDIOFLOW_NETWORK=studioflow-platform`. The isolated local example has a different network name; change it deliberately for production.
- `RESTIC_REPOSITORY=s3:https://EXTERNAL_ENDPOINT/BUCKET/PREFIX`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, and `BACKUP_S3_REGION`. Use an external bucket distinct from primary storage.
- Keep `STUDIOFLOW_BACKUP_HOST=studioflow` stable across deployments so retention groups do not change with temporary container hostnames. Give rehearsal backups a different repository prefix and host label from production.
- Initially set `STUDIOFLOW_ENABLE_CREATION=0`, `STUDIOFLOW_ENABLE_COMPOSITION=0`, `STUDIOFLOW_ENABLE_EXPORTS=0`. Keep `STUDIOFLOW_VALIDATED_2K_MODELS` empty until the provider capability benchmark is approved and passes.

Provision a long random Restic password at `/srv/studioflow/secrets/restic-password`, owned by UID/GID 10001 with mode 0600. Retain an independent recovery copy outside mvserver. Neither the password nor API/storage keys belong in Git or chat. The password file is not included in application backups.

The runner must retain `self-hosted`, `linux`, `x64`, `studioflow-production` labels and have Python 3, a local Docker Engine and Compose supporting `up --wait`. The release helper can prepare the top-level `/srv/studioflow` directory. Docker access is privileged; use the existing trusted production runner. The application and object-store containers run under separate service UIDs.

## Missing platform network on the first push

`network studioflow-platform declared as external, but could not be found` means the application's shared infrastructure network is absent on that Docker host. A normal push replaces an already prepared application; it does not provision PostgreSQL/SeaweedFS or perform the first migration.

1. Configure the complete platform `PROD_ENV_FILE` in both GitHub environments, **production** and **production-maintenance**, using matching database/storage settings and `STUDIOFLOW_NETWORK=studioflow-platform`. The local example uses `studioflow-local-platform`; do not mix that name into production by accident.
2. In GitHub **Actions**, select **Explicit StudioFlow platform maintenance**, choose **Run workflow**, select **main**, and choose **action: prepare**. This creates the network and persistent PostgreSQL/SeaweedFS services while leaving the existing application running.
3. Follow **First production switch** below to finish migration, external backup/restore verification, activation and resume. If the stack was already activated and only its infrastructure was stopped or removed, investigate that change and recover it before rerunning normal deployment.

Creating an empty bridge with `docker network create` alone does not provide the database/object storage or satisfy the cutover checks. The infrastructure Compose project owns this network. Retrying the same deployment before preparing the stack cannot complete it.

Runtime settings are now validated in a read-only container with `--network none`, using Compose's resolved environment over standard input. Configuration checks need no production volume or platform network and do not print credentials. Deployment then checks the exact network name resolved by Compose before starting preflight, backup or replacement containers; a missing network reports the maintenance action explicitly. Non-prepare maintenance actions perform the same check.

## Isolated rehearsal

1. Run the local stack from the root README on port 8001 with its own named volumes and network. Do not overwrite `.env`, `data/`, or the original source checkout. `docker build` must select `--target runtime` for the application or `--target operations` for operator tooling.
2. Obtain a stopped or drained **copy** of the real workspace, including uploads, outputs and logos. Use the snapshot command below; copying only a live `.sqlite3` file omits committed WAL data. Audit reports missing images, identifier conflicts, relationships, accounting totals and an estimated staging requirement. The estimate excludes ongoing growth, extra copies and external repository capacity; provision those separately.

   ```sh
   python -m studioflow.operations.cli audit --source /path/to/legacy-copy
   python -m studioflow.operations.cli snapshot --source /path/to/stopped-copy --target .migration/rehearsal
   docker compose --env-file .env.platform -p studioflow-local run --rm ops import --source /migration/rehearsal
   ```

3. The importer is resumable for the exact same frozen source. Different source hashes, a populated target, unresolved references or inadequate space block import. It preserves public IDs, users, sessions, catalog/memory, approvals, history, submission keys, queue ordering and usage totals. Every referenced image is checksum-verified; historical thumbnails are generated locally. Historical listing slots remain unassigned. Interrupted paid work is held for review.
4. Imported work remains in maintenance. Verify the report before allowing any processing. A disposable UI rehearsal can use `ops resume --local-test`, but its copied queue will then run in that stack's configured mode. Never reuse those rehearsal volumes as the final production target.
5. Run an external backup and fresh-stack restore as described below. Review one real product's quality separately, with authorization for the paid request. Check shape, colour, fine detail, scale, pack count, lighting, exact text and mobile readability. Start with one lifestyle request and derive MAIN/dimensions locally if an approved cutout exists. Retain measured API size and cost; only then set the validated model list.

## External backup and restore rehearsal

The `ops` service mounts a secrets directory. Put `restic-password` inside `.secrets/` locally, or the production path above. The following local commands use `.env.platform` with real external backup configuration. Repository initialization is explicit and refuses an existing repository:

```sh
docker compose --env-file .env.platform -p studioflow-local run --rm ops backup-init
docker compose --env-file .env.platform -p studioflow-local run --rm ops backup
```

Keep the completed `snapshot_id` from the backup response. A failed producer stream does not count as a successful snapshot. Backup resumes writers only if they were not already in maintenance. If capture/draining fails, maintenance stays enabled for investigation. Check owner-only `GET /api/operations`, scheduler logs and the external repository; an RPO of one day is a target that requires successful backups.

For restore, create a separate `.env.restore` with new primary database credentials, bucket and network, `STUDIOFLOW_ENV_FILE=.env.restore`, `STUDIOFLOW_NETWORK=studioflow-restore-platform`, and `STUDIOFLOW_PORT=8002`. Retain the original external Restic configuration/password. Use a new Compose project:

```sh
docker compose --env-file .env.restore -p studioflow-restore up -d --wait postgres seaweedfs
docker compose --env-file .env.restore -p studioflow-restore run --rm ops restore --snapshot-id COMPLETED_SNAPSHOT_ID > .tmp/restore-verification.json
```

**Do not run `ops init` in the restore target.** Restore requires an empty PostgreSQL database; it creates a missing private bucket and refuses a nonempty bucket/staging area. It verifies the database dump, every object checksum, pending transfers, record counts and accounting totals, then leaves maintenance on. Images stream through bounded temporary files; a second full object store is not staged locally. The target is disposable if a restore fails part-way; preserve diagnostics and use another empty target rather than merging incomplete results.

Validate login, catalog/history, images and queue recovery on the restored stack before production activation. `ops resume --local-test` is for that isolated validation only. The proof JSON contains the restored source hash and verification details; keep it from the successful restore. `--local-test` Restic repositories produce a proof that **cannot** activate production.

## First production switch

The `Explicit StudioFlow platform maintenance` workflow accepts `prepare`, `migrate`, `backup`, `activate`, `resume`, and `status`. Each run builds/tests the exact `main` revision, downloads the verified bundle without checkout on mvserver, and takes the shared deployment lock. Plan a maintenance window for the final backup/import/restore cycle. Use the same tested revision and configuration throughout; a step already staged with different settings intentionally refuses reuse.

1. **Prepare:** run action `prepare`. It creates service-owned directories, starts persistent infrastructure and initializes an empty PostgreSQL schema/bucket. It leaves the original application running. Provision the recovery password and initialize the external Restic repository once using the staged operations Compose file. For example, after selecting the actual staged directory:

   ```sh
   export STUDIOFLOW_ENV_FILE=/srv/studioflow/platform-releases/REVISION-prepare/runtime.env
   export STUDIOFLOW_OPS_IMAGE=studioflow-ops:REVISION
   docker compose --env-file "$STUDIOFLOW_ENV_FILE" -p studioflow-operations -f /srv/studioflow/platform-releases/REVISION-prepare/operations.yml run --rm ops backup-init
   ```

2. **Migrate:** during the agreed window, stop new submissions and let active provider requests finish, then run action `migrate`. It identifies the original container's data bind mount, stops that container, freezes the final workspace under `migration/final-REVISION`, imports and reconciles it. The original directory stays intact. Migration failures leave service stopped for investigation. A resumed original service invalidates reuse of that final snapshot.
3. **Backup:** run action `backup` against the final imported state. Restore that completed external snapshot into a fresh, isolated stack using the procedure above. Verify it and copy its proof to `/srv/studioflow/migration/restore-verification.json`, readable by UID 10001. A proof from an earlier source copy cannot activate a different final source hash.
4. **Activate:** run action `activate`. It checks schema/storage, reconciliation and the external restore proof. It records readiness while leaving writes paused.
5. **Resume:** run action `resume`. It executes the normal guarded deployment of the tested application, verifies container health and image identity, starts the backup scheduler, then explicitly reopens writes. Check login, catalog, library, original media URLs, approvals, revisions, A+, usage totals and queue state.
6. Enable `STUDIOFLOW_ENABLE_EXPORTS`, then `STUDIOFLOW_ENABLE_CREATION`, then `STUDIOFLOW_ENABLE_COMPOSITION` through configuration and tested deployments. Observe health, pending transfers, unknown attempts, costs and representative outputs after each stage. Keep native 2K gated until its paid validation succeeds.

An existing named-volume/unmanaged legacy container is not automatically adopted; map a frozen source explicitly and rehearse its import before cutover. For a truly fresh installation, use an empty stack, external backup/restore, activation and normal deployment without the legacy-import step.

## Subsequent pushes and failure behaviour

CI checks Python/JavaScript, PostgreSQL/SeaweedFS integration, encrypted restoration, 100,000-record pagination, browser acceptance and container startup. It packages both runtime and operations images with a checksum manifest. mvserver validates the bundle, current-main revision, settings, schema, storage and activation marker **before stopping** the running application. It also requires a completed external backup before replacement.

Normal deployment changes neither infrastructure nor schema. It replaces the application only, waits for its health check, verifies image identity and updates the backup scheduler. There is no automatic downgrade or database restoration if replacement fails. Local staging archives are diagnostic backups, not substitutes for the external database/object backup.

Keep application configuration, external backup access and the recovery password recoverable independently. The daily scheduler runs at 03:00 Asia/Kolkata and catches up a missed scheduled backup on restart. Retention is seven daily, four weekly and three monthly snapshots. Cleanup is suspended during backups; linked assets and uncertain receipts are retained. Expired ZIPs and verified abandoned uploads are eligible for cleanup.

After any new production write, **never restore the old SQLite snapshot automatically**. Preserve current database/object state and reconcile new jobs, receipts, approvals and uploads before recovery. A failed paid request without a saved response may already have been charged; review it before explicitly creating another request.
