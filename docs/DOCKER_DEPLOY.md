# StudioFlow deployment and upgrade

The image packages the current application, `studioflow/` and `static/` only. `.env`, workspace data, tests, temporary backups and `References/` are excluded. The container runs as UID/GID `10001:10001`. Docker was unavailable in the implementation environment; build and container startup still need to be checked on the deployment host.

## Automatic deployment to mvserver

The workflow files are prepared in the repository. They become active after they are pushed to GitHub and the following one-time host/repository settings are complete. This local setup does not change an existing GitHub runner's labels, repository secrets, or server directories.

Every push to `main` runs `ci.yml` through the deployment workflow: Python tests, JavaScript tests, a Docker build and an authenticated demo-generation smoke check. Only a successful run packages its exact image and deployment inputs for `mvserver`. Pull requests run the same checks on GitHub-hosted runners and cannot enter the production deploy job. The `Run workflow` button is also supported for `main`.

### One-time setup

1. On `mvserver`, use a Linux x64 self-hosted runner with Python 3.10+, Docker Engine, a Docker Compose plugin supporting `up --wait`, and access to the local Docker daemon. The runner must be version **2.327.1 or newer**, as required by [download-artifact v7](https://github.com/actions/download-artifact#v7---whats-new). Add the custom runner label **`studioflow-production`** under GitHub repository **Settings → Actions → Runners → your mvserver runner**. Standard `self-hosted`, `linux` and `x64` labels must also be present.
2. Create a GitHub environment named **`production`** under repository Settings → Environments. Limit its deployment branches to **`main`**. Required reviewers are optional; leaving them unset allows automatic deployment after CI passes.
3. Store the complete current production `.env` content as the **secret** `PROD_ENV_FILE` in that environment (or retain an existing repository secret with that name). Do not store it in repository variables. Include the existing accounts, owners, mode, key, secure-cookie setting and intended bind address. An empty secret is allowed only after `/srv/studioflow/.env` has already been provisioned; it uses that private file. Existing GitHub repository variables are deliberately not used as a credential fallback.
4. Create the persistent root directory once. Run the following on `mvserver` as the **same account that runs the Actions runner service**, or replace the user/group arguments with that service account:

   ```sh
   sudo install -d -m 700 -o "$(id -u)" -g "$(id -g)" /srv/studioflow
   docker info >/dev/null
   docker compose version
   python3 --version
   ```

   The parent directory belongs to the runner. The deployment helper sets ownership of **only `/srv/studioflow/data` and its contents** to container UID/GID 10001, with directory mode 750 and file mode 640. It does not grant world-write access.

5. Ensure sufficient disk space for the incoming Docker image, one complete data archive per deployment and, on first adoption, a second copy of the existing workspace. Allow the runner to access GitHub's artifact endpoints; these may require additional firewall allowances described in the action's documentation. Backups and old images are retained; arrange retention separately after confirming your restore requirements.
6. Push the reviewed workflow/script changes to `main`, or merge a PR after the checks pass. Watch the first run under **Actions → Deploy StudioFlow to mvserver**. No repository push or production activation was performed by the local implementation.

### What happens to the existing workspace

The deploy job does **not** check source out on the production runner. It downloads the same-run artifact to the runner's temporary directory and validates checksums, revision and the image's revision label. It skips obsolete releases when a newer commit is already on `main`. Deployments share a GitHub concurrency group and a host-side file lock.

Before stopping the existing app, the script checks the new Compose configuration and validates runtime settings in a one-off container that does not import the app or start workers. It discovers the existing `studioflow` container's `/app/data` bind mount and Compose project; it does not inspect or print the container's environment variables.

For the first adoption, it stops the old container gracefully, archives the whole stopped workspace, copies it to **an empty** `/srv/studioflow/data`, retains the original directory, sets the container ownership and starts the tested image. A nonempty destination containing another workspace causes an error before stopping production. Named Docker volumes, unmanaged containers and remote Docker daemons require a deliberate migration instead of automatic adoption; this deployment is for the local Docker daemon on `mvserver`.

Later deployments back up the permanent workspace in place, then recreate the service using the tested image. The health gate checks the new container's Docker health status and image identity. Automatic SDK or paid-job retries are not introduced. Queued jobs resume normally; an interrupted in-flight provider call may have an unknown outcome, so avoid scheduling deployments during large active batches.

Runtime files are separate from source:

```text
/srv/studioflow/
  .env                 private last-successful configuration
  current.json         last successful revision, image ID and backup
  data/                SQLite, uploads, outputs and logos
  backups/             complete data archives and previous-container metadata
  releases/            candidate Compose files, private config and manifests
```

### Deployment failures

Build, test, checksum, stale-revision and runtime-validation failures leave the current service running. If a backup fails after the old service stops, the script restarts that original service without replacing its database. If replacement or the new healthcheck fails, the job fails and retains the new data and backup for diagnosis. It does **not** automatically restore a database or restart an older image against a possibly migrated database, because accepted jobs or schema changes may already exist.

Use the failed run's deploy-step output and `/srv/studioflow/backups` to diagnose. The workflow avoids dumping production container logs into GitHub and does not prune Docker images or unrelated services. Preserve failed adoption copies until you have reconciled them; a later run intentionally refuses to merge two nonempty workspaces.

The production workflow deploys `deploy/compose.production.yml`. The root `docker-compose.yml` remains available for local development and manual deployment; after adoption, do not launch that root Compose file against the old checkout's data directory, as it would create a second workspace.

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
