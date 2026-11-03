# StudioFlow deployment and upgrade

The image packages the current application, `studioflow/` and `static/` only. `.env`, workspace data, tests, temporary backups and `References/` are excluded. The container runs as UID/GID `10001:10001`. Docker was unavailable in the implementation environment; build and container startup still need to be checked on the deployment host.

## First deployment

1. Create `.env` from `.env.example` if it does not already exist. Configure `TEAM_USERS` and the desired owners. Start with `APP_MODE=demo` in a fresh workspace.
2. Ensure `./data` is writable by the container user. On Linux, create the intended data directory and assign it to UID/GID 10001. Do not change permissions on an unrelated parent directory. Docker Desktop bind-mount permissions depend on host file sharing.
3. Run `docker compose config --quiet` to validate the configuration without printing credentials, then `docker compose up -d --build`.
4. Verify `docker compose ps` reports a healthy service and `http://127.0.0.1:8000/api/health` returns `{"status":"ok"}`. Sign in and complete a demo generation and review.

Compose publishes only on `127.0.0.1` by default. For a team deployment, configure the intended network access and HTTPS reverse proxy, then set `COOKIE_SECURE=1`. Compose forces authentication even if local anonymous demo settings are present in `.env`. Keep `.env` and backups accessible only to trusted workspace administrators.

Live mode requires an API key, account access to the selected model, and billing configured with the provider. Check the new workflow in an isolated demo workspace before enabling live use. The local balance estimate is advisory and does not stop jobs at a spending limit.

## Upgrade an existing workspace

1. Stop new submissions and let active requests finish. Stop the service gracefully (`docker compose stop` for Compose); all application workers must be stopped before an offline data copy. The configured grace period is 240 seconds. Interrupted provider calls are not automatically retried after restart.
2. Back up the deployed source/image version, `.env` separately, and the complete data directory, including SQLite WAL/SHM files if present, uploads, outputs and `brand_logos`. Copy a stopped workspace or use SQLite's backup API for the database; copying only a live `.sqlite3` file is not a consistent backup.
3. Retain the current data directory and existing credentials. Deploy the updated current-app source. Do not copy `References/New StudioFlow` over the app and do not import its database.
4. Run the regression commands in `README.md` and rehearse startup migrations on a copy of the database. Start with `STUDIOFLOW_ENABLE_APLUS=0` and `STUDIOFLOW_ENABLE_USAGE=0` if a staged rollout is preferred.
5. Rebuild/restart the service. Startup applies additive migrations 3 and 4 in a transaction. Existing catalog, memory, sessions, idempotency records and jobs are retained. Accepted queued work resumes using the configured mode, so never change a live workspace to demo as a migration test.
6. Verify health, sign-in, catalog entries, product memory, the existing image library and job statuses. Enable the new flags and restart when ready. In a separate demo workspace, save an A+ profile and generate both output sizes. Refresh the browser after the deployment to load the new scripts.
7. Before the first live batch, verify access to the selected model and the actual OpenAI account balance. Use a deliberately small, approved live request to check the provider integration and metering on that account.

The integration backup created on this workstation is `.tmp/before-v13-20261102-085430/`: `source.zip`, `manifest.json`, a consistent `studio.sqlite3` backup and `assets.zip`. It excludes `.env`. A rehearsal ran both migrations twice against a copy; `.tmp/migration-verification.json` records unchanged original rows and successful integrity checks. The live data directory was not migrated by that rehearsal.

## Rollback and recovery

For an issue confined to a new screen, disable its feature flag and restart the updated app. This retains all new records and keeps accepted jobs supported. A+ disablement does not cancel jobs; usage dashboard disablement does not stop metering.

Prefer fixing forward if new production work has been saved. A full rollback requires stopping all workers and restoring the matching source and complete data backup together. Restoring the pre-upgrade data discards work created after that backup, so first preserve the current workspace and obtain the owner's decision about that data. Do not run old code against a queue containing A+ jobs or assume a schema downgrade is supported. Old code also cannot record the new usage receipts.

For failed or interrupted generations, review the render error and OpenAI Billing before explicitly submitting another request. A missing response cannot prove that the provider performed no work. Known receipts remain available even if a later image export failed; unknown costs are retained for reconciliation.
