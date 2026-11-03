"""SQLite-backed task claiming with live-worker heartbeats.

Queued tasks survive restarts. Interrupted paid requests are failed rather than
automatically repeated: their provider-side outcome may be unknown.
"""
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class RenderQueue:
    def __init__(self, db, handler, max_workers=3, *, lease_seconds=90, poll_seconds=0.25):
        self.db = db
        self.handler = handler
        self.max_workers = max_workers
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.owner = uuid.uuid4().hex
        self.stopping = threading.Event()
        self.pool = None
        self.thread = None

    def start(self):
        self.pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix='render')
        self.heartbeat()
        self.thread = threading.Thread(target=self.run, name='render-dispatcher', daemon=True)
        self.thread.start()

    def heartbeat(self):
        with self.db() as c:
            c.execute('INSERT INTO workers(id, heartbeat) VALUES(?,?) '
                      'ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat',
                      (self.owner, time.time()))

    def recover(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE renders SET status='failed', error=?, worker_id='' "
                      "WHERE status='running' AND NOT EXISTS "
                      '(SELECT 1 FROM workers w WHERE w.id=renders.worker_id AND w.heartbeat>=?)',
                      ('Worker stopped during generation. Review before submitting another paid request.',
                       time.time() - self.lease_seconds))
            c.execute('DELETE FROM workers WHERE heartbeat<?', (time.time() - self.lease_seconds,))
            c.execute("UPDATE usage_ledger SET status='unknown',finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                      "WHERE status IN ('started','response_received') AND render_id IN (SELECT id FROM renders WHERE status='failed')")
            c.execute("UPDATE jobs SET status='finished' WHERE status='running' AND NOT EXISTS "
                      "(SELECT 1 FROM renders WHERE renders.job_id=jobs.id AND status IN ('queued','running'))")

    def claim(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT * FROM renders WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute("UPDATE renders SET status='running',worker_id=? WHERE id=? AND status='queued'",
                      (self.owner, row['id']))
            return dict(row)

    def execute(self, row):
        """Make unexpected handler failures terminal without repeating paid work."""
        try:
            self.handler(row, self.owner)
        except Exception:
            logger.exception('Render handler failed for %s', row['id'])
            with self.db() as c:
                c.execute("UPDATE renders SET status='failed',error=? "
                          "WHERE id=? AND worker_id=? AND status='running'",
                          ('Image generation failed. Check server logs before retrying.',
                           row['id'], self.owner))
                c.execute("UPDATE jobs SET status='finished' WHERE id=? AND NOT EXISTS "
                          "(SELECT 1 FROM renders WHERE job_id=? AND status IN ('queued','running'))",
                          (row['job_id'], row['job_id']))

    def run(self):
        futures = set()
        last_heartbeat = 0
        while not self.stopping.is_set() or futures:
            try:
                if time.monotonic() - last_heartbeat >= min(10, self.lease_seconds / 3):
                    self.heartbeat()
                    self.recover()
                    last_heartbeat = time.monotonic()
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
                logger.exception('Render queue dispatch failed')
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
        with self.db() as c:
            c.execute('DELETE FROM workers WHERE id=?', (self.owner,))
