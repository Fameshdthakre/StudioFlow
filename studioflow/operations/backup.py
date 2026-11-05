"""Consistent snapshots streamed to Restic; immutable object bytes are never staged twice."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from urllib.parse import urlsplit
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from ..services import assets, maintenance
from ..services.common import now
from ..storage import checksum


def postgres_env(workspace):
    url = make_url(workspace.settings.database_url)
    env = dict(
        os.environ,
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "",
        PGPASSWORD=url.password or "",
        PGDATABASE=url.database or "",
        PGCONNECT_TIMEOUT="10",
    )
    if url.query.get("sslmode"):
        env["PGSSLMODE"] = url.query["sslmode"]
    return env


def command(name):
    return os.getenv("STUDIOFLOW_" + name.upper(), name)


def restic_env(*, external=True):
    env = dict(os.environ)
    repo = env.get("RESTIC_REPOSITORY", "")
    password_file = env.get("RESTIC_PASSWORD_FILE", "")
    if not repo or external and not repo.startswith("s3:https://"):
        raise RuntimeError("Configure an external HTTPS S3 Restic repository")
    if not password_file or not Path(password_file).is_file():
        raise RuntimeError(
            "Configure RESTIC_PASSWORD_FILE and retain its recovery password independently"
        )
    for target, source in (
        ("AWS_ACCESS_KEY_ID", "BACKUP_S3_ACCESS_KEY_ID"),
        ("AWS_SECRET_ACCESS_KEY", "BACKUP_S3_SECRET_ACCESS_KEY"),
        ("AWS_DEFAULT_REGION", "BACKUP_S3_REGION"),
    ):
        if source in env:
            env[target] = env[source]
    if external and not all(
        env.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    ):
        raise RuntimeError("External backup credentials are missing")
    return env


@contextmanager
def operation_lock(workspace):
    with maintenance.gate_engine(workspace.settings.database_url).connect() as c:
        if not c.execute(text("SELECT pg_try_advisory_lock(193700, 3)")).scalar():
            raise RuntimeError("Another storage operation is in progress")
        try:
            yield
        finally:
            c.execute(text("SELECT pg_advisory_unlock(193700, 3)"))


def drain(workspace, timeout=600):
    from ..jobs import RenderQueue

    recovery = RenderQueue(
        workspace.db,
        lambda *args: None,
        platform=True,
        saved_receipt=lambda rid: (
            workspace.staging / (rid + ".receipt.json")
        ).is_file(),
    )
    deadline = time.monotonic() + timeout
    while True:
        recovery.recover(during_maintenance=True)
        with workspace.db() as c:
            c.execute(
                "UPDATE assets SET state='pending',transfer_worker_id='' WHERE state='transferring' AND NOT EXISTS (SELECT 1 FROM workers w WHERE w.id=assets.transfer_worker_id AND heartbeat>=?)",
                (time.time() - 90,),
            )
            c.execute(
                "UPDATE exports SET status='queued',worker_id='' WHERE status='running' AND NOT EXISTS (SELECT 1 FROM workers w WHERE w.id=exports.worker_id AND heartbeat>=?)",
                (time.time() - 90,),
            )
            active = c.execute(
                "SELECT COUNT(*) FROM renders WHERE status='running'"
            ).fetchone()[0]
            active += c.execute(
                "SELECT COUNT(*) FROM exports WHERE status='running'"
            ).fetchone()[0]
            active += c.execute(
                "SELECT COUNT(*) FROM assets WHERE state='transferring'"
            ).fetchone()[0]
        if not active:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Timed out draining work; maintenance remains enabled for investigation"
            )
        time.sleep(1)


def capture(workspace, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    with workspace.db() as c:
        already_paused = maintenance.paused(c)
    maintenance.set_value(workspace, "maintenance", "1")
    # Wait for every request already admitted by the shared request gate.
    with maintenance.exclusive(workspace):
        drain(workspace)
        database = target / "database.dump"
        subprocess.run(
            [
                command("pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-acl",
                "--file",
                str(database),
            ],
            env=postgres_env(workspace),
            check=True,
        )
        with workspace.db() as c:
            manifest = {
                "version": 1,
                "created_at": now(),
                "database_sha256": checksum(database),
                "assets": [
                    dict(r) for r in c.execute("SELECT * FROM assets ORDER BY id")
                ],
                "pending": [],
                "counts": {},
            }
            for table in (
                "jobs",
                "renders",
                "catalog_products",
                "product_memory",
                "product_rules",
                "sessions",
                "usage_ledger",
                "assets",
            ):
                manifest["counts"][table] = c.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            manifest["cost_microusd"] = int(
                c.execute(
                    "SELECT COALESCE(SUM(cost_microusd),0) FROM usage_ledger"
                ).fetchone()[0]
            )
            source = c.execute(
                "SELECT value FROM operations WHERE key='migration_source'"
            ).fetchone()
            manifest["migration_source"] = source[0] if source else "new-workspace"
        pending = {
            a["staging_file"] for a in manifest["assets"] if a["state"] != "ready"
        }
        pending.update(p.name for p in workspace.staging.glob("*.receipt.json"))
        (target / "pending").mkdir()
        for name in sorted(pending):
            path = workspace.staging / name
            if path.parent != workspace.staging or not path.is_file():
                raise RuntimeError("Pending transfer file is missing")
            # These are immutable. A hard link avoids a second copy when possible.
            dest = target / "pending" / name
            try:
                os.link(path, dest)
            except OSError:
                shutil.copyfile(path, dest)
            manifest["pending"].append(
                {"name": name, "sha256": checksum(dest), "bytes": dest.stat().st_size}
            )
        assets.atomic_write(target / "manifest.json", json.dumps(manifest).encode())
    if not already_paused:
        maintenance.set_value(workspace, "maintenance", "0")
    return manifest


class VerifiedReader:
    def __init__(self, stream):
        self.stream, self.digest = stream, hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.digest.update(data)
        return data


def stream_archive(workspace, snapshot_dir, output):
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    with tarfile.open(fileobj=output, mode="w|") as archive:
        archive.add(root / "manifest.json", arcname="manifest.json", recursive=False)
        if checksum(root / "database.dump") != manifest["database_sha256"]:
            raise RuntimeError("Database dump checksum mismatch")
        archive.add(root / "database.dump", arcname="database.dump", recursive=False)
        for entry in manifest["pending"]:
            path = root / "pending" / entry["name"]
            if path.parent != root / "pending" or checksum(path) != entry["sha256"]:
                raise RuntimeError("Pending transfer checksum mismatch")
            archive.add(path, arcname="pending/" + entry["name"], recursive=False)
        for asset in manifest["assets"]:
            if asset["state"] != "ready":
                continue
            info = tarfile.TarInfo("objects/" + asset["object_key"])
            info.size, info.mode = asset["byte_size"], 0o600
            with workspace.storage.open(asset["object_key"]) as stream:
                reader = VerifiedReader(stream)
                archive.addfile(info, reader)
                if reader.digest.hexdigest() != asset["sha256"]:
                    raise RuntimeError("Object checksum mismatch: " + asset["id"])


def backup(workspace, *, external=True):
    env = restic_env(external=external)
    env.setdefault("RESTIC_CACHE_DIR", str(workspace.data / "restic-cache"))
    if (
        external
        and urlsplit(workspace.settings.s3_endpoint).hostname
        == urlsplit(env["RESTIC_REPOSITORY"][3:]).hostname
    ):
        raise RuntimeError(
            "Backup repository must be external to the primary image store"
        )
    with operation_lock(workspace):
        maintenance.set_value(workspace, "backup_running", "1")
        snapshot_id = uuid.uuid4().hex
        target = workspace.data / "backup-staging" / snapshot_id
        try:
            capture(workspace, target)
            args = [
                command("restic"),
                "backup",
                "--json",
                "--host",
                env.get("STUDIOFLOW_BACKUP_HOST", "studioflow"),
                "--tag",
                "studioflow",
                "--tag",
                snapshot_id,
                "--stdin-from-command",
                "--stdin-filename",
                "studioflow.tar",
                "--",
                sys.executable,
                "-m",
                "studioflow.operations.cli",
                "stream",
                "--snapshot-dir",
                str(target),
            ]
            result = subprocess.run(args, env=env, capture_output=True, text=True)
            if result.returncode:
                diagnostic = result.stderr
                for name in (
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "RESTIC_PASSWORD",
                ):
                    if env.get(name):
                        diagnostic = diagnostic.replace(env[name], "[redacted]")
                print(diagnostic, file=sys.stderr)
                raise RuntimeError("Restic backup failed; snapshot was not recorded")
            summaries = [
                json.loads(line)
                for line in result.stdout.splitlines()
                if line.startswith("{")
            ]
            receipt = next(
                (x for x in summaries if x.get("message_type") == "summary"), None
            )
            if not receipt or not receipt.get("snapshot_id"):
                raise RuntimeError("Restic did not return a completed snapshot")
            saved = {
                "snapshot_id": receipt["snapshot_id"],
                "completed_at": now(),
                "external": external,
            }
            maintenance.set_value(workspace, "last_backup", json.dumps(saved))
            subprocess.run(
                [
                    command("restic"),
                    "forget",
                    "--host",
                    env.get("STUDIOFLOW_BACKUP_HOST", "studioflow"),
                    "--tag",
                    "studioflow",
                    "--group-by",
                    "host,paths",
                    "--keep-daily",
                    "7",
                    "--keep-weekly",
                    "4",
                    "--keep-monthly",
                    "3",
                    "--prune",
                ],
                env=env,
                check=True,
            )
            shutil.rmtree(target)
            return saved
        finally:
            maintenance.set_value(workspace, "backup_running", "0")


def restore(workspace, snapshot_id, *, external=True):
    """Restore only into an empty database/bucket/staging area, never over production."""
    from ..persistence.connection import engine

    env = restic_env(external=external)
    env.setdefault("RESTIC_CACHE_DIR", str(workspace.data / "restic-cache"))
    with engine(workspace.settings.database_url).connect() as c:
        count = c.scalar(
            text(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'"
            )
        )
        if count:
            raise RuntimeError("Restore requires an empty PostgreSQL database")
    if workspace.settings.storage_backend == "s3":
        workspace.storage.initialize()
    if any(workspace.staging.iterdir()) or workspace.storage.has_objects():
        raise RuntimeError("Restore requires empty image storage and staging")
    proc = subprocess.Popen(
        [command("restic"), "dump", snapshot_id, "studioflow.tar"],
        env=env,
        stdout=subprocess.PIPE,
    )
    root = workspace.data / "restore" / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            entries = iter(archive)
            first = next(entries)
            if first.name != "manifest.json" or first.size > 100 * 1024 * 1024:
                raise RuntimeError("Invalid StudioFlow backup manifest")
            manifest = json.load(archive.extractfile(first))
            if manifest.get("version") != 1 or set(manifest["counts"]) != {
                "jobs",
                "renders",
                "catalog_products",
                "product_memory",
                "product_rules",
                "sessions",
                "usage_ledger",
                "assets",
            }:
                raise RuntimeError("Unsupported backup manifest")
            expected = {"database.dump": manifest["database_sha256"]}
            pending = {"pending/" + p["name"]: p for p in manifest["pending"]}
            objects = {
                "objects/" + a["object_key"]: a
                for a in manifest["assets"]
                if a["state"] == "ready"
            }
            expected.update({k: v["sha256"] for k, v in pending.items()})
            expected.update({k: v["sha256"] for k, v in objects.items()})
            seen = set()
            for entry in entries:
                if (
                    not entry.isfile()
                    or entry.name not in expected
                    or entry.name in seen
                ):
                    raise RuntimeError("Unexpected or duplicate archive entry")
                temporary = root / "incoming"
                with archive.extractfile(entry) as src, temporary.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if checksum(temporary) != expected[entry.name]:
                    raise RuntimeError("Restored archive checksum mismatch")
                if entry.name == "database.dump":
                    temporary.replace(root / "database.dump")
                elif entry.name in pending:
                    name = pending[entry.name]["name"]
                    if Path(name).name != name:
                        raise RuntimeError("Unsafe pending filename")
                    temporary.replace(workspace.staging / name)
                else:
                    asset = objects[entry.name]
                    workspace.storage.put(
                        asset["object_key"], temporary, asset["mime"], asset["sha256"]
                    )
                    temporary.unlink()
                seen.add(entry.name)
        # Consume tar padding before waiting; otherwise the producer can block
        # on a full pipe after the tar reader has seen the end-of-archive marker.
        while proc.stdout.read(64 * 1024):
            pass
        if proc.wait() or seen != set(expected):
            raise RuntimeError("Incomplete Restic archive; restore was not published")
        subprocess.run(
            [
                command("pg_restore"),
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                "--dbname",
                postgres_env(workspace)["PGDATABASE"],
                str(root / "database.dump"),
            ],
            env=postgres_env(workspace),
            check=True,
        )
        maintenance.set_value(workspace, "maintenance", "1")
        with workspace.db() as c:
            c.execute("DELETE FROM workers")
            # ZIPs are temporary derivatives, not part of the image backup.
            c.execute(
                "UPDATE exports SET status='queued',worker_id='' WHERE expires_at>?",
                (time.time(),),
            )
            for table, count in manifest["counts"].items():
                if c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != count:
                    raise RuntimeError("Restored record count differs")
            total = c.execute(
                "SELECT COALESCE(SUM(cost_microusd),0) FROM usage_ledger"
            ).fetchone()[0]
            if total != manifest["cost_microusd"]:
                raise RuntimeError("Restored accounting differs")
        for asset in manifest["assets"]:
            if asset["state"] == "ready":
                with assets.materialize(workspace, asset["id"]):
                    pass
        result = {
            "snapshot_id": snapshot_id,
            "verified_at": now(),
            "external": external,
            "asset_count": len(manifest["assets"]),
            "counts": manifest["counts"],
            "migration_source": manifest["migration_source"],
            "database_sha256": manifest["database_sha256"],
        }
        assets.atomic_write(
            root / "verification.json", json.dumps(result, indent=2).encode()
        )
        maintenance.set_value(workspace, "restore_verified", json.dumps(result))
        maintenance.set_value(workspace, "backup_running", "0")
        return result
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
