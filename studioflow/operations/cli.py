"""Operator commands. No command ever calls a paid image provider."""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command as alembic_command
from alembic.config import Config

from ..persistence.connection import check_schema
from ..persistence.repository import ensure_user
from ..services import maintenance
from ..settings import Settings
from ..workspace import Workspace


def workspace():
    base = Path(__file__).resolve().parents[2]
    ws = Workspace(Settings.load(base), base)
    for folder in (
        ws.data,
        ws.staging,
        ws.exports,
        ws.uploads,
        ws.outputs,
        ws.brand_logos,
    ):
        folder.mkdir(parents=True, exist_ok=True)
    return ws


def initialize(ws):
    if not ws.settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[1] / "persistence/alembic"),
    )
    config.attributes["database_url"] = ws.settings.database_url
    alembic_command.upgrade(config, "head")
    if ws.settings.storage_backend == "s3":
        ws.storage.initialize()
    with ws.db() as c:
        for username in (*ws.settings.team, "Demo user"):
            ensure_user(c, username)
    return {"schema": "0001_platform", "storage": ws.settings.storage_backend}


def preflight(ws, *, production=True):
    if not ws.settings.database_url or ws.settings.storage_backend != "s3":
        raise RuntimeError(
            "This release requires PostgreSQL and private S3 storage. Complete the explicit maintenance migration before deployment."
        )
    check_schema(ws.settings.database_url)
    ws.storage.health()
    state = maintenance.state(ws)
    if production and state.get("platform_ready", {}).get("value") != "postgres-s3-v1":
        raise RuntimeError(
            "Production cutover is not verified. Import, reconcile and restore an external backup before activating this release."
        )
    with ws.db() as c:
        pending = c.execute(
            "SELECT staging_file FROM assets WHERE state<>'ready'"
        ).fetchall()
    for row in pending:
        if not (ws.staging / row["staging_file"]).is_file():
            raise RuntimeError("A pending asset has no recoverable staging file")
    return {"schema": "0001_platform", "storage": "postgres-s3-v1", "ready": True}


def activate(ws, proof_path):
    preflight(ws, production=False)
    proof = json.loads(Path(proof_path).read_text("utf-8"))
    state = maintenance.state(ws)
    source = state.get("migration_source", {}).get("value", "new-workspace")
    if (
        not proof.get("external")
        or not proof.get("verified_at")
        or proof.get("migration_source") != source
    ):
        raise RuntimeError(
            "A successful external restore verification for this migration is required"
        )
    if source != "new-workspace" and not state.get("migration_verified"):
        raise RuntimeError("Migration reconciliation has not completed")
    maintenance.set_value(ws, "restore_verified", json.dumps(proof))
    maintenance.set_value(ws, "platform_ready", "postgres-s3-v1")
    return {"ready": True, "maintenance": "still enabled until explicit resume"}


def scheduler(ws):
    from .backup import backup
    from .cleanup import cleanup

    timezone = ZoneInfo("Asia/Kolkata")
    last_attempt = None
    while True:
        current = datetime.now(timezone)
        target = current.replace(hour=3, minute=0, second=0, microsecond=0)
        if target <= current:
            saved = maintenance.state(ws).get("last_backup", {}).get("value")
            completed = (
                datetime.fromisoformat(json.loads(saved)["completed_at"]).astimezone(
                    timezone
                )
                if saved
                else None
            )
            if last_attempt == target.date() or completed and completed >= target:
                target += timedelta(days=1)
        while datetime.now(timezone) < target:
            time.sleep(
                min(30, max(1, (target - datetime.now(timezone)).total_seconds()))
            )
        try:
            last_attempt = target.date()
            print(json.dumps(backup(ws)), flush=True)
            print(json.dumps(cleanup(ws)), flush=True)
        except Exception as exc:
            maintenance.set_value(
                ws,
                "backup_error",
                type(exc).__name__ + ": operation failed; inspect service logs",
            )
            print(
                "Scheduled backup failed; previous snapshots were retained",
                file=sys.stderr,
                flush=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "audit",
            "snapshot",
            "init",
            "import",
            "pause",
            "resume",
            "preflight",
            "activate",
            "backup",
            "backup-init",
            "restore",
            "stream",
            "cleanup",
            "scheduler",
        ],
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--proof", type=Path)
    parser.add_argument(
        "--local-test",
        action="store_true",
        help="Permit a local Restic test repository; cannot activate production",
    )
    args = parser.parse_args()
    if args.command in ("audit", "snapshot"):
        from .migration import audit, snapshot

        if not args.source or args.command == "snapshot" and not args.target:
            parser.error("--source and snapshot --target are required")
        result = (
            audit(args.source)
            if args.command == "audit"
            else snapshot(args.source, args.target)
        )
    else:
        ws = workspace()
        if args.command == "init":
            result = initialize(ws)
        elif args.command == "import":
            from .migration import migrate

            if not args.source:
                parser.error("--source is required")
            result = migrate(ws, args.source)
        elif args.command in ("pause", "resume"):
            if args.command == "resume":
                preflight(ws, production=not args.local_test)
            maintenance.set_value(
                ws, "maintenance", "1" if args.command == "pause" else "0"
            )
            result = {"maintenance": args.command == "pause"}
        elif args.command == "preflight":
            result = preflight(ws, production=not args.local_test)
        elif args.command == "activate":
            if not args.proof:
                parser.error("--proof is required")
            result = activate(ws, args.proof)
        elif args.command == "backup-init":
            import subprocess
            from .backup import command, restic_env

            subprocess.run(
                [command("restic"), "init"],
                env=restic_env(external=not args.local_test),
                check=True,
            )
            result = {"initialized": True}
        elif args.command == "backup":
            from .backup import backup

            result = backup(ws, external=not args.local_test)
        elif args.command == "restore":
            from .backup import restore

            if not args.snapshot_id:
                parser.error("--snapshot-id is required")
            result = restore(ws, args.snapshot_id, external=not args.local_test)
        elif args.command == "stream":
            from .backup import stream_archive

            if not args.snapshot_dir:
                parser.error("--snapshot-dir is required")
            stream_archive(ws, args.snapshot_dir, sys.stdout.buffer)
            return
        elif args.command == "cleanup":
            from .cleanup import cleanup

            result = cleanup(ws)
        else:
            scheduler(ws)
            return
    print(json.dumps(result, indent=2))
    if args.command in ("audit", "snapshot") and result["conflicts"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
