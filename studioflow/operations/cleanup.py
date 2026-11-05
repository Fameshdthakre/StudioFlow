"""Conservative retention: linked assets and uncertain paid receipts are never deleted."""

import time
from datetime import datetime, timedelta, timezone

from .backup import operation_lock
from ..services import maintenance


def cleanup(workspace):
    expired = datetime.now(timezone.utc) - timedelta(days=7)
    with operation_lock(workspace):
        state = maintenance.state(workspace)
        if (
            state.get("backup_running", {}).get("value") == "1"
            or state.get("maintenance", {}).get("value") == "1"
        ):
            return {"skipped": "Maintenance or backup is active"}
        removed_exports, removed_assets, removed_staging = 0, 0, 0
        with workspace.db() as c:
            c.execute("BEGIN IMMEDIATE")
            for row in c.execute(
                "SELECT id FROM exports WHERE expires_at<? AND status<>'running'",
                (time.time(),),
            ).fetchall():
                (workspace.exports / (row["id"] + ".zip")).unlink(missing_ok=True)
                c.execute(
                    "DELETE FROM asset_links WHERE owner_type='export' AND owner_id=?",
                    (row["id"],),
                )
                c.execute("DELETE FROM exports WHERE id=?", (row["id"],))
                removed_exports += 1
            abandoned = c.execute(
                """SELECT * FROM assets a WHERE role IN ('original_upload','product_reference','style_reference','cutout')
                AND created_at<? AND NOT EXISTS (SELECT 1 FROM asset_links l WHERE l.asset_id=a.id)
                AND NOT EXISTS (SELECT 1 FROM legacy_assets l WHERE l.asset_id=a.id)
                AND NOT EXISTS (SELECT 1 FROM assets child WHERE child.parent_id=a.id)
                AND NOT EXISTS (SELECT 1 FROM renders r WHERE r.source_asset_id=a.id OR r.master_asset_id=a.id OR r.output_asset_id=a.id OR r.thumbnail_asset_id=a.id)
                LIMIT 100""",
                (expired.isoformat(),),
            ).fetchall()
            for row in abandoned:
                workspace.storage.delete(row["object_key"])
                (workspace.staging / row["staging_file"]).unlink(missing_ok=True)
                c.execute("DELETE FROM assets WHERE id=?", (row["id"],))
                removed_assets += 1
            for row in c.execute("SELECT staging_file FROM assets WHERE state='ready'"):
                path = workspace.staging / row["staging_file"]
                if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                    path.unlink()
                    removed_staging += 1
            for row in c.execute("SELECT id FROM renders WHERE status='completed'"):
                for suffix in (
                    ".receipt.json",
                    ".master.png",
                    ".final.jpg",
                    ".thumb.webp",
                ):
                    path = workspace.staging / (row["id"] + suffix)
                    if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                        path.unlink()
                        removed_staging += 1
        return {
            "expired_exports": removed_exports,
            "abandoned_assets": removed_assets,
            "staged_files": removed_staging,
        }
