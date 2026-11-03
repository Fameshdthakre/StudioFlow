# Deploy StudioFlow on mvserver

The workflows deploy a tested Docker image from `main` to the existing Linux `mvserver` runner. Persistent state lives at `/srv/studioflow`, outside the Actions checkout. These files must be committed/pushed and the host/GitHub settings below configured before automatic deployment is active.

## One-time setup

1. Keep the existing runner and add the custom label `studioflow-production`. The workflow also selects `self-hosted`, `linux` and `x64`. Use an Actions runner that supports the pinned Node 24 actions.
2. Ensure the runner service account can run Python 3 and access the local Docker Engine and Docker Compose v2 (`up --wait` is required). Remote Docker contexts are rejected.
3. Create the state directory owned by that runner account. Replace the two placeholders with the actual service account and group:

   ```sh
   sudo install -d -m 0700 -o RUNNER_ACCOUNT -g RUNNER_GROUP /srv/studioflow
   ```

4. In GitHub, create the `production` environment and restrict it to `main`. Add the environment secret `PROD_ENV_FILE`, containing the complete runtime environment file. Start from the root `.env.example`, set real team accounts and the intended demo/live mode. Configure the existing API key privately if using live mode. Use `COOKIE_SECURE=1` behind HTTPS and set the intended bind address; the default is `127.0.0.1`. Do not commit credentials.
5. Keep the current `studioflow` container and its data bind mount intact for the first deployment. The script discovers `/app/data` from that container. It supports an existing Compose-managed container with one data bind mount. Named volumes or an unrecognized container require an operator-managed migration first.

## Release flow

Pull requests run Python and browser unit tests, build the Docker image, and exercise authentication, shipped browser modules/template, A+ demo generation and image export in a disposable container. A push to `main` runs those checks and packages the tested image with a checksum manifest and deployment script. A manual run on `main` follows the same checks.

Only the deploy job uses `mvserver`; it downloads the verified bundle without checking out or cleaning the repository. Deployments are serialized and are not canceled during replacement. The script checks that the revision is still the current `main` before stopping production.

Before replacement, the script validates configuration, stops the current container with a 240-second grace period, and archives the complete data directory. On first adoption it copies the stopped old bind mount into the empty permanent data directory, retaining the original source and backup. It refuses to merge two populated workspaces. Container data permissions are set for UID/GID 10001.

The new container must pass its own health check and match the tested image before the deployment is recorded as successful. Startup resumes queued work; interrupted running requests are not automatically retried. Deployment verification itself makes no paid provider requests.

```text
/srv/studioflow/
├── .env                 # Current private runtime configuration
├── .deploy.lock
├── current.json         # Last successful revision/image/backup
├── data/                # SQLite plus uploads, outputs and brand logos
├── backups/             # Stopped-workspace archives and previous metadata
└── releases/            # Compose/configuration and manifest per attempt
```

Backups contain the whole workspace. Copy verified backups to your normal backup destination and manage retention outside the runner checkout. Release directories also contain private runtime configuration and must remain restricted.

## Failures and recovery

If validation or backup fails before replacement, the script leaves the original container intact and resumes it when necessary. If replacement has begun, it preserves the current data and reports failure. It does not restore the database or restart an older worker automatically: doing so could lose new writes or repeat already charged requests.

Inspect the failed Actions step, container status/logs and `/srv/studioflow/current.json`. Preserve the current data before any manual recovery. Prefer a corrected deployment when possible. To use an older image, first verify that it supports the current schema and render types, then explicitly select its saved release configuration and the permanent data directory. A backup restore is a separate operator decision requiring all workers to be stopped and any intervening writes/queued work reconciled.

## Local/manual Compose

The root `docker-compose.yml` builds local sources and binds `./data`. It remains available for development and manual operation; automatic production deployment uses `deploy/compose.production.yml` and the tested image instead. Both keep `uvicorn app:app` as the application entry point.
