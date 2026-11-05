"""PostgreSQL queue claims and worker leases, with SQLite preview compatibility.

Queued tasks survive restarts. Interrupted paid requests are failed rather than
automatically repeated: their provider-side outcome may be unknown.
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class RenderQueue:
    def __init__(
        self,
        db,
        handler,
        max_workers=3,
        *,
        lease_seconds=90,
        poll_seconds=0.25,
        platform=False,
        auxiliary=None,
        saved_receipt=None,
        heartbeat_db=None,
    ):
        self.db = db
        self.heartbeat_db = heartbeat_db or db
        self.handler = handler
        self.max_workers = max_workers
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.owner = uuid.uuid4().hex
        self.stopping = threading.Event()
        self.pool = None
        self.thread = None
        self.platform = platform
        self.auxiliary = auxiliary
        self.saved_receipt = saved_receipt
        self.aux_pool = None
        self.heartbeat_thread = None
        self.heartbeat_stopping = threading.Event()

    def start(self):
        self.pool = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="render"
        )
        self.aux_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export")
        self.heartbeat()
        self.heartbeat_thread = threading.Thread(
            target=self.renew_lease, name="worker-heartbeat", daemon=True
        )
        self.heartbeat_thread.start()
        self.thread = threading.Thread(
            target=self.run, name="render-dispatcher", daemon=True
        )
        self.thread.start()

    def heartbeat(self):
        with self.heartbeat_db() as c:
            c.execute(
                "INSERT INTO workers(id, heartbeat) VALUES(?,?) "
                "ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat",
                (self.owner, time.time()),
            )

    def renew_lease(self):
        while not self.heartbeat_stopping.wait(min(10, self.lease_seconds / 3)):
            try:
                self.heartbeat()
            except Exception:
                logger.exception("Could not renew worker lease")

    def recover(self, *, during_maintenance=False):
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            if self.platform or getattr(c, "dialect", "sqlite") == "postgresql":
                from .services.maintenance import paused

                if paused(c) and not during_maintenance:
                    return
                if self.saved_receipt:
                    stale = c.execute(
                        "SELECT id FROM renders WHERE status='running' AND NOT EXISTS (SELECT 1 FROM workers w WHERE w.id=renders.worker_id AND w.heartbeat>=?)",
                        (time.time() - self.lease_seconds,),
                    ).fetchall()
                    for row in stale:
                        if self.saved_receipt(row["id"]):
                            c.execute(
                                "UPDATE renders SET status='transferring',worker_id='',transfer_after=0 WHERE id=?",
                                (row["id"],),
                            )
                c.execute(
                    """UPDATE renders SET status='transferring',worker_id='',transfer_after=0
                    WHERE status='running' AND (master_asset_id IS NOT NULL OR operation<>'generate')
                    AND NOT EXISTS (SELECT 1 FROM workers w WHERE w.id=renders.worker_id AND w.heartbeat>=?)""",
                    (time.time() - self.lease_seconds,),
                )
                c.execute("""UPDATE renders SET status='failed',error='The background image failed; review it before retrying'
                    WHERE status='queued' AND depends_on IN (SELECT id FROM renders WHERE status='failed')""")
            c.execute(
                "UPDATE renders SET status='failed', error=?, worker_id='' "
                "WHERE status='running' AND NOT EXISTS "
                "(SELECT 1 FROM workers w WHERE w.id=renders.worker_id AND w.heartbeat>=?)",
                (
                    "Worker stopped during generation. Review before submitting another paid request.",
                    time.time() - self.lease_seconds,
                ),
            )
            c.execute(
                "DELETE FROM workers WHERE heartbeat<?",
                (time.time() - self.lease_seconds,),
            )
            c.execute(
                "UPDATE usage_ledger SET status=CASE WHEN received_at IS NULL THEN 'unknown' ELSE 'failed' END,finished_at=? "
                "WHERE status IN ('started','response_received') AND render_id IN (SELECT id FROM renders WHERE status='failed')",
                (datetime.now(timezone.utc).isoformat(),),
            )
            c.execute(
                "UPDATE jobs SET status='finished' WHERE status='running' AND NOT EXISTS "
                "(SELECT 1 FROM renders WHERE renders.job_id=jobs.id AND status IN ('queued','running','transferring'))"
            )

    def claim(self):
        with self.db() as c:
            postgres = getattr(c, "dialect", "sqlite") == "postgresql"
            if not postgres:
                c.execute("BEGIN IMMEDIATE")
            if self.platform or postgres:
                from .services.maintenance import paused

                if postgres:
                    c.execute("SELECT pg_advisory_xact_lock_shared(193700, 2)")
                if paused(c):
                    return None
                sql = """SELECT * FROM renders WHERE (status='queued' OR (status='transferring' AND transfer_after<=?))
                    AND (depends_on IS NULL OR depends_on IN (SELECT id FROM renders WHERE status='completed'))
                    ORDER BY enqueue_seq LIMIT 1"""
                if postgres:
                    sql += " FOR UPDATE SKIP LOCKED"
                row = c.execute(sql, (time.time(),)).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM renders WHERE status='queued' ORDER BY created_at,rowid LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE renders SET status='running',worker_id=? WHERE id=? AND status IN ('queued','transferring')",
                (self.owner, row["id"]),
            )
            return dict(row)

    def execute(self, row):
        """Make unexpected handler failures terminal without repeating paid work."""
        try:
            self.handler(row, self.owner)
        except Exception:
            logger.exception("Render handler failed for %s", row["id"])
            with self.db() as c:
                c.execute(
                    "UPDATE renders SET status='failed',error=? "
                    "WHERE id=? AND worker_id=? AND status='running'",
                    (
                        "Image generation failed. Check server logs before retrying.",
                        row["id"],
                        self.owner,
                    ),
                )
                c.execute(
                    "UPDATE jobs SET status='finished' WHERE id=? AND NOT EXISTS "
                    "(SELECT 1 FROM renders WHERE job_id=? AND status IN ('queued','running','transferring'))",
                    (row["job_id"], row["job_id"]),
                )

    def run(self):
        futures = set()
        auxiliary_future = None
        last_heartbeat = 0
        while (
            not self.stopping.is_set()
            or futures
            or (auxiliary_future and not auxiliary_future.done())
        ):
            try:
                if time.monotonic() - last_heartbeat >= min(10, self.lease_seconds / 3):
                    self.recover()
                    last_heartbeat = time.monotonic()
                    if auxiliary_future and auxiliary_future.done():
                        completed_auxiliary = auxiliary_future
                        auxiliary_future = None
                        try:
                            completed_auxiliary.result()
                        except Exception:
                            # Consume the failed task once so later transfer and
                            # export passes can recover instead of re-raising it.
                            logger.exception("Background transfer/export pass failed")
                    if (
                        self.auxiliary
                        and auxiliary_future is None
                        and not self.stopping.is_set()
                    ):
                        auxiliary_future = self.aux_pool.submit(
                            self.auxiliary, self.owner
                        )
                for future in list(futures):
                    if future.done():
                        futures.remove(future)
                        # Handler failures must be visible instead of being silently discarded.
                        future.result()
                while len(futures) < self.max_workers and not self.stopping.is_set():
                    row = self.claim()
                    if row is None:
                        break
                    futures.add(self.pool.submit(self.execute, row))
            except Exception:
                logger.exception("Render queue dispatch failed")
            # Keep leases alive while already-running tasks drain on shutdown.
            if self.stopping.is_set():
                time.sleep(self.poll_seconds)
            else:
                self.stopping.wait(self.poll_seconds)

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
        if self.pool:
            self.pool.shutdown(wait=True)
        if self.aux_pool:
            self.aux_pool.shutdown(wait=True)
        self.heartbeat_stopping.set()
        if self.heartbeat_thread:
            self.heartbeat_thread.join()
        with self.db() as c:
            c.execute("DELETE FROM workers WHERE id=?", (self.owner,))
