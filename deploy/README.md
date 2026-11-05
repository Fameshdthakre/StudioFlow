# Deploy StudioFlow on mvserver

The platform release uses PostgreSQL 18 and private SeaweedFS, with runtime state under `/srv/studioflow`. It requires the **explicit first migration** described in [the deployment and recovery runbook](../docs/DOCKER_DEPLOY.md).

| File | Purpose |
| --- | --- |
| `compose.infrastructure.yml` | Pinned PostgreSQL/SeaweedFS, private network, service ownership |
| `compose.infrastructure.production.yml` | Bind persistent infrastructure to `/srv/studioflow` |
| `compose.production.yml` | Replace only the tested application image |
| `compose.operations.yml` | Import/backup/restore commands and daily backup service |
| `../docker-compose.yml` | Isolated local stack, same service images, default port 8001 |

Keep the runner labels `self-hosted`, `linux`, `x64`, `studioflow-production`. Configure `PROD_ENV_FILE` in the `production` and `production-maintenance` GitHub environments. See the runbook for complete platform settings, the external backup password file, rehearsal and first cutover.

Regular pushes run CI and deploy only after schema/storage/restore readiness checks pass. An incompatible state stops deployment before the running service is stopped. The first release therefore intentionally fails that gate until maintenance is complete. No workflow automatically restores old SQLite data or discards new production writes.
