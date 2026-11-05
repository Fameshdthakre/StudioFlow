"""Read-only SQLite audit and resumable import into an isolated PostgreSQL database."""

import json
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from ..persistence.connection import engine, check_schema
from ..persistence.repository import (
    ensure_product,
    ensure_user,
    link_asset,
    link_snapshot,
)
from ..persistence.schema import metadata
from ..composition import thumbnail
from ..services import assets, maintenance
from ..services.common import now
from ..storage import checksum

TABLES = (
    "brands",
    "aplus_profiles",
    "catalog_products",
    "memory_settings",
    "product_memory",
    "memory_audit",
    "sessions",
    "jobs",
    "renders",
    "product_rules",
    "idempotency",
    "usage_ledger",
    "usage_budget",
)


def source_connection(source):
    path = (Path(source) / "studio.sqlite3").resolve()
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    c.execute("BEGIN")
    return c


def safe_source(source, name):
    base = Path(source).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path == base or Path(name).is_absolute():
        raise ValueError("Unsafe source asset path")
    return path


def audit(source):
    source = Path(source).resolve()
    conflicts, files, counts = [], {}, {}
    with closing(source_connection(source)) as c:
        present = {
            r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            conflicts.append("SQLite integrity check failed")
        if (
            "assets" in present
            and c.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        ):
            conflicts.append(
                "Source already contains platform assets; use platform backup/restore, not the legacy importer"
            )
        for table in TABLES:
            counts[table] = (
                c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if table in present
                else 0
            )
        for table, column in (
            ("catalog_products", "sku"),
            ("product_memory", "sku"),
            ("brands", "name"),
            ("aplus_profiles", "name"),
        ):
            if table in present:
                duplicates = c.execute(
                    f'SELECT UPPER("{column}") FROM "{table}" GROUP BY UPPER("{column}") HAVING COUNT(*)>1'
                ).fetchall()
                if duplicates:
                    conflicts.append(
                        f"{table}: {len(duplicates)} duplicate identifiers"
                    )
        render_ids = {r[0] for r in c.execute("SELECT id FROM renders")}
        job_ids = {r[0] for r in c.execute("SELECT id FROM jobs")}
        parents = {
            r["id"]: r["parent_id"]
            for r in c.execute("SELECT id,parent_id FROM renders")
        }
        checked = set()
        for rid in parents:
            seen, current = set(), rid
            while current and current in parents and current not in checked:
                if current in seen:
                    conflicts.append("Cyclic revision relationship: " + rid)
                    break
                seen.add(current)
                current = parents[current]
            checked.update(seen)
        for table in ("catalog_products", "product_memory"):
            if table in present:
                columns = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
                if (
                    "brand_id" in columns
                    and c.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE brand_id<>'' AND brand_id NOT IN (SELECT id FROM brands)"
                    ).fetchone()[0]
                ):
                    conflicts.append(table + ": missing brand references")
        for table in ("jobs", "renders", "sessions"):
            if c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user IS NULL OR user=''"
            ).fetchone()[0]:
                conflicts.append(table + ": missing historical ownership")
        if (
            "product_rules" in present
            and c.execute(
                "SELECT COUNT(*) FROM product_rules WHERE source_render_id<>'' AND source_render_id NOT IN (SELECT id FROM renders)"
            ).fetchone()[0]
        ):
            conflicts.append("Product rules reference missing renders")

        def reference(folder, filename):
            if not filename:
                return
            name = folder + "/" + filename
            if name in files:
                return
            try:
                path = safe_source(source, name)
                if path.parent != (source / folder) or not path.is_file():
                    raise ValueError("Missing asset")
                files[name] = {"sha256": checksum(path), "bytes": path.stat().st_size}
            except (OSError, ValueError):
                conflicts.append("Missing or unsafe referenced asset: " + name)

        for row in c.execute("SELECT * FROM renders"):
            row = dict(row)
            if (
                row["job_id"] not in job_ids
                or row.get("parent_id")
                and row["parent_id"] not in render_ids
            ):
                conflicts.append("Broken render relationship: " + row["id"])
            for folder, column in (
                ("uploads", "source"),
                ("outputs", "file"),
                ("outputs", "original"),
            ):
                reference(folder, row.get(column))
            try:
                snapshot = json.loads(row.get("memory_snapshot") or "{}")
                for profile in (snapshot.get("branding"), snapshot.get("aplus")):
                    if profile:
                        reference("brand_logos", profile.get("logo_file"))
            except (ValueError, AttributeError):
                conflicts.append("Invalid memory snapshot: " + row["id"])
        for table in ("brands", "aplus_profiles"):
            if table in present:
                for row in c.execute(f"SELECT logo_file FROM {table}"):
                    reference("brand_logos", row[0])
        if "usage_ledger" in present:
            orphaned = c.execute(
                "SELECT COUNT(*) FROM usage_ledger WHERE render_id NOT IN (SELECT id FROM renders) OR job_id NOT IN (SELECT id FROM jobs)"
            ).fetchone()[0]
            if orphaned:
                conflicts.append(f"Usage ledger has {orphaned} orphaned attempts")
            cost = c.execute(
                "SELECT COALESCE(SUM(cost_microusd),0) FROM usage_ledger"
            ).fetchone()[0]
        else:
            cost = 0
        active = c.execute(
            "SELECT COUNT(*) FROM renders WHERE status='running'"
        ).fetchone()[0]
    return {
        "version": 1,
        "source_sha256": checksum(source / "studio.sqlite3"),
        "counts": counts,
        "files": files,
        "asset_bytes": sum(f["bytes"] for f in files.values()),
        "cost_microusd": cost,
        "interrupted_requests": active,
        "conflicts": conflicts,
        "required_free_bytes": sum(f["bytes"] for f in files.values()) * 2
        + (source / "studio.sqlite3").stat().st_size * 4,
    }


def snapshot(source, target):
    """Source must be drained/stopped. SQLite backup includes committed WAL pages."""
    source, target = Path(source).resolve(), Path(target).resolve()
    if target == source or target.is_relative_to(source):
        raise ValueError("Snapshot must be outside the original workspace")
    target.mkdir(parents=True, exist_ok=False)
    with (
        closing(source_connection(source)) as src,
        closing(sqlite3.connect(target / "studio.sqlite3")) as dst,
    ):
        src.backup(dst)
    for directory in ("uploads", "outputs", "brand_logos"):
        if (source / directory).is_dir():
            if (source / directory).is_symlink() or any(
                p.is_symlink() for p in (source / directory).rglob("*")
            ):
                raise ValueError(
                    "Resolve symbolic links before taking a migration snapshot"
                )
            shutil.copytree(source / directory, target / directory, symlinks=False)
    report = audit(target)
    (target / "audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def migrate(workspace, source):
    from .backup import operation_lock

    if (
        not workspace.settings.database_url
        or workspace.settings.storage_backend != "s3"
    ):
        raise RuntimeError("Migration requires PostgreSQL and private S3 storage")
    check_schema(workspace.settings.database_url)
    with operation_lock(workspace):
        return _migrate(workspace, source)


def _migrate(workspace, source):
    wal = Path(source) / "studio.sqlite3-wal"
    if wal.is_file() and wal.stat().st_size:
        raise ValueError(
            "Import a frozen snapshot created with the snapshot command; the source still has WAL data"
        )
    report = audit(source)
    if report["conflicts"]:
        raise ValueError("Migration blocked: " + "; ".join(report["conflicts"]))
    if shutil.disk_usage(workspace.data).free < report["required_free_bytes"]:
        raise ValueError("Insufficient free staging capacity")
    workspace.storage.health()
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        marker = c.execute(
            "SELECT value FROM operations WHERE key='migration_source'"
        ).fetchone()
        if marker and marker[0] != report["source_sha256"]:
            raise ValueError(
                "Target belongs to a different migration snapshot; restore into another empty stack"
            )
        if not marker and any(
            c.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in (*TABLES, "assets")
        ):
            raise ValueError(
                "Target contains application data; refusing to merge workspaces"
            )
    maintenance.set_value(workspace, "maintenance", "1")
    maintenance.set_value(workspace, "migration_source", report["source_sha256"])
    mapped, thumbnails = {}, {}
    for name, info in report["files"].items():
        path = safe_source(source, name)
        if checksum(path) != info["sha256"]:
            raise ValueError("Source changed during migration")
        aid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            "studioflow:legacy:" + report["source_sha256"] + ":" + name,
        ).hex
        role = (
            "logo"
            if name.startswith("brand_logos/")
            else "finished_image"
            if name.startswith("outputs/")
            else "product_reference"
        )
        assets.publish(workspace, path, role, "migration", aid=aid)
        assets.map_legacy(workspace, name, aid)
        mapped[name] = aid
        if name.startswith("outputs/"):
            thumbnail_path = workspace.staging / (aid + ".legacy-thumb.webp")
            thumbnail(path, thumbnail_path)
            thumbnail_id = uuid.uuid5(
                uuid.NAMESPACE_URL, "studioflow:legacy-thumbnail:" + aid
            ).hex
            assets.publish(
                workspace,
                thumbnail_path,
                "thumbnail",
                "migration",
                aid=thumbnail_id,
                parent_id=aid,
            )
            thumbnails[name] = thumbnail_id
            thumbnail_path.unlink(missing_ok=True)
    with closing(source_connection(source)) as src:
        present = {
            r[0]
            for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        with workspace.db() as c:
            for table in TABLES:
                if table not in present:
                    continue
                for row in src.execute(f'SELECT * FROM "{table}"'):
                    values = dict(row)
                    for field in ("user", "created_by", "updated_by"):
                        if values.get(field):
                            ensure_user(c, values[field])
                    if values.get("sku"):
                        ensure_product(c, values["sku"])
            ensure_user(c, "migration")
        catalog = (
            {
                r["sku"].upper(): dict(r)
                for r in src.execute("SELECT * FROM catalog_products")
            }
            if "catalog_products" in present
            else {}
        )
        parent_updates = []
        render_values = []
        for table_name in TABLES:
            if table_name not in present:
                continue
            table = metadata.tables[table_name]
            rows = src.execute(
                f'SELECT rowid AS legacy_order,* FROM "{table_name}" ORDER BY rowid'
            )
            for row in rows:
                raw = dict(row)
                values = {k: v for k, v in raw.items() if k in table.c}
                for column in table.c:
                    if column.foreign_keys and values.get(column.name) == "":
                        values[column.name] = None
                    if (
                        column.name in values
                        and values[column.name] is None
                        and not column.nullable
                    ):
                        values[column.name] = (
                            str(column.server_default.arg)
                            if column.server_default is not None
                            else ""
                        )
                if "product_id" in table.c:
                    values["product_id"] = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "studioflow:product:" + raw["sku"].strip().upper(),
                    ).hex
                if table_name == "renders":
                    if values.get("parent_id"):
                        parent_updates.append((values["id"], values["parent_id"]))
                    values["parent_id"] = None
                    values["enqueue_seq"] = raw["legacy_order"]
                    values["sku_snapshot"] = values["sku"]
                    values["asin_snapshot"] = (
                        catalog.get(values["sku"].upper(), {}).get("asin") or ""
                    )
                    values["source_asset_id"] = mapped.get(
                        "uploads/" + (values.get("source") or "")
                    )
                    values["output_asset_id"] = mapped.get(
                        "outputs/" + (values.get("file") or "")
                    )
                    values["master_asset_id"] = values["output_asset_id"]
                    values["thumbnail_asset_id"] = thumbnails.get(
                        "outputs/" + (values.get("file") or "")
                    )
                    # Historical slots are intentionally unassigned until reviewed.
                    values["variant"] = ""
                    if values["status"] == "running":
                        values.update(
                            status="failed",
                            worker_id="",
                            error="Imported interrupted request. Review provider receipt before resubmitting.",
                        )
                    render_values.append(values)
                with engine(workspace.settings.database_url).begin() as c:
                    c.execute(insert(table).values(**values).on_conflict_do_nothing())
        with workspace.db() as c:
            for rid, parent in parent_updates:
                c.execute("UPDATE renders SET parent_id=? WHERE id=?", (parent, rid))
            for row in render_values:
                for field in (
                    "source_asset_id",
                    "master_asset_id",
                    "output_asset_id",
                    "thumbnail_asset_id",
                ):
                    link_asset(c, row.get(field), "render", row["id"])
                link_snapshot(
                    c, json.loads(row.get("memory_snapshot") or "{}"), row["id"]
                )
            c.execute(
                "UPDATE usage_ledger SET status=CASE WHEN received_at IS NULL THEN 'unknown' ELSE 'failed' END WHERE status IN ('started','response_received') AND render_id IN (SELECT id FROM renders WHERE status='failed')"
            )
            c.execute(
                "UPDATE jobs SET status='finished' WHERE NOT EXISTS (SELECT 1 FROM renders WHERE renders.job_id=jobs.id AND status IN ('queued','running','transferring'))"
            )
            c.execute(
                "SELECT setval('render_enqueue_sequence',GREATEST(1,(SELECT COALESCE(MAX(enqueue_seq),0) FROM renders)+1),false)"
            )
            c.execute(
                "SELECT setval(pg_get_serial_sequence('usage_ledger','id'),GREATEST(1,(SELECT COALESCE(MAX(id),0) FROM usage_ledger)+1),false)"
            )
    result = verify(workspace, report)
    if checksum(Path(source) / "studio.sqlite3") != report["source_sha256"]:
        raise ValueError(
            "The source database changed during import; keep maintenance enabled and investigate"
        )
    maintenance.set_value(workspace, "migration_verified", json.dumps(result))
    return result


def verify(workspace, report):
    counts = {}
    with engine(workspace.settings.database_url).connect() as c:
        for name in TABLES:
            counts[name] = c.scalar(
                select(func.count()).select_from(metadata.tables[name])
            )
        total = int(
            c.scalar(
                select(
                    func.coalesce(
                        func.sum(metadata.tables["usage_ledger"].c.cost_microusd), 0
                    )
                )
            )
        )
    if counts != report["counts"] or total != report["cost_microusd"]:
        raise ValueError(
            "Migration reconciliation failed: counts or usage totals differ"
        )
    for name, expected in report["files"].items():
        aid = assets.legacy_id(workspace, name)
        with assets.materialize(workspace, aid) as path:
            if checksum(path) != expected["sha256"]:
                raise ValueError("Migrated image checksum mismatch")
    return {
        "source_sha256": report["source_sha256"],
        "counts": counts,
        "cost_microusd": total,
        "verified_assets": len(report["files"]),
        "verified_at": now(),
    }
