"""Durable queue concurrency, recovery and graceful-shutdown regressions."""

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from studioflow.database import connect, initialize
from studioflow.jobs import RenderQueue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.path = self.base / "queue.sqlite3"
        initialize(self.path)
        self.db = lambda: connect(self.path)

    def add_render(self, render_id, *, status="queued", worker="", job=None):
        job = job or f"job-{render_id}"
        with self.db() as c:
            c.execute(
                "INSERT OR IGNORE INTO jobs(id,user,kind,total) VALUES(?,?,?,?)",
                (job, "test-user", "main", 1),
            )
            c.execute(
                "INSERT INTO renders(id,job_id,user,kind,status,worker_id,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (render_id, job, "test-user", "main", status, worker, render_id),
            )
        return job

    def render(self, render_id):
        with self.db() as c:
            return dict(
                c.execute("SELECT * FROM renders WHERE id=?", (render_id,)).fetchone()
            )

    def job_status(self, job_id):
        with self.db() as c:
            return c.execute(
                "SELECT status FROM jobs WHERE id=?", (job_id,)
            ).fetchone()[0]

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Queue did not reach the expected state before the deadline")

    def test_two_owners_atomically_claim_each_render_once(self):
        queues = [RenderQueue(self.db, lambda *_: None) for _ in range(2)]
        for index in range(40):
            self.add_render(f"render-{index:02}")
        for queue in queues:
            queue.heartbeat()
        barrier = threading.Barrier(2)

        def drain(queue):
            barrier.wait(timeout=5)
            claimed = []
            while row := queue.claim():
                claimed.append((row["id"], queue.owner))
            return claimed

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(drain, queue) for queue in queues]
            claims = [item for future in futures for item in future.result(timeout=10)]
        self.assertEqual(len(claims), 40)
        self.assertEqual(len({render_id for render_id, _ in claims}), 40)
        self.assertIsNone(queues[0].claim())
        for render_id, owner in claims:
            row = self.render(render_id)
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["worker_id"], owner)

    def test_recovery_preserves_queued_work_and_healthy_running_work(self):
        queue = RenderQueue(self.db, lambda *_: None, lease_seconds=30)
        with self.db() as c:
            c.execute("INSERT INTO workers VALUES(?,?)", ("healthy", time.time()))
            c.execute("INSERT INTO workers VALUES(?,?)", ("stale", time.time() - 60))
        queued_job = self.add_render("queued")
        active_job = self.add_render("active", status="running", worker="healthy")
        stale_job = self.add_render("stale", status="running", worker="stale")
        unknown_job = self.add_render("unknown", status="running", worker="missing")

        queue.recover()

        self.assertEqual(self.render("queued")["status"], "queued")
        self.assertEqual(self.render("active")["status"], "running")
        self.assertEqual(self.render("active")["worker_id"], "healthy")
        self.assertEqual(self.job_status(queued_job), "running")
        self.assertEqual(self.job_status(active_job), "running")
        for render_id, job_id in [("stale", stale_job), ("unknown", unknown_job)]:
            row = self.render(render_id)
            self.assertEqual(row["status"], "failed")
            self.assertIn("Review before submitting another paid request", row["error"])
            self.assertEqual(self.job_status(job_id), "finished")
        self.assertEqual(queue.claim()["id"], "queued")
        self.assertIsNone(queue.claim())
        with self.db() as c:
            owners = {row["id"] for row in c.execute("SELECT id FROM workers")}
        self.assertEqual(owners, {"healthy"})

    def test_real_queue_processes_demo_asset_and_finishes_job(self):
        source = self.base / "source.png"
        output = self.base / "demo.jpg"
        Image.new("RGB", (120, 120), "red").save(source)
        job = self.add_render("demo")

        def demo_handler(row, owner):
            with Image.open(source) as image:
                image.resize((240, 240)).save(output, "JPEG")
            with self.db() as c:
                c.execute(
                    "UPDATE renders SET status='completed',file=? WHERE id=? AND worker_id=?",
                    (output.name, row["id"], owner),
                )
                c.execute(
                    "UPDATE jobs SET status='finished' WHERE id=?", (row["job_id"],)
                )

        queue = RenderQueue(self.db, demo_handler, max_workers=1, poll_seconds=0.01)
        queue.start()
        try:
            self.wait_for(lambda: self.render("demo")["status"] == "completed")
        finally:
            queue.stop()
        self.assertEqual(self.job_status(job), "finished")
        with Image.open(output) as image:
            self.assertEqual(image.size, (240, 240))
            self.assertEqual(image.format, "JPEG")
        with self.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM workers").fetchone()[0], 0)

    def test_shutdown_maintains_lease_while_active_task_drains(self):
        started = threading.Event()
        release = threading.Event()
        stop_finished = threading.Event()
        calls = []
        self.add_render("active")
        self.add_render("waiting")

        def handler(row, owner):
            calls.append(row["id"])
            started.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Test did not release the active task")
            with self.db() as c:
                c.execute(
                    "UPDATE renders SET status='completed' WHERE id=? AND worker_id=? AND status='running'",
                    (row["id"], owner),
                )

        # Allow filesystem/scheduler jitter while still observing multiple lease windows.
        queue = RenderQueue(
            self.db, handler, max_workers=1, lease_seconds=1, poll_seconds=0.01
        )
        observer = RenderQueue(self.db, lambda *_: None, lease_seconds=1)
        queue.start()
        stopper = None
        try:
            self.assertTrue(started.wait(timeout=5))
            with self.db() as c:
                initial_heartbeat = c.execute(
                    "SELECT heartbeat FROM workers WHERE id=?", (queue.owner,)
                ).fetchone()[0]

            def stop_queue():
                queue.stop()
                stop_finished.set()

            stopper = threading.Thread(target=stop_queue)
            stopper.start()
            self.wait_for(lambda: queue.stopping.is_set())
            # More than twice the lease duration: an unmaintained owner expires.
            deadline = time.monotonic() + 2.2
            while time.monotonic() < deadline:
                observer.recover()
                self.assertEqual(self.render("active")["status"], "running")
                self.assertFalse(stop_finished.is_set())
                time.sleep(0.02)
            with self.db() as c:
                heartbeat = c.execute(
                    "SELECT heartbeat FROM workers WHERE id=?", (queue.owner,)
                ).fetchone()[0]
            self.assertGreater(heartbeat, initial_heartbeat)
            self.assertEqual(self.render("waiting")["status"], "queued")
        finally:
            release.set()
            if stopper:
                stopper.join(timeout=5)
            else:
                queue.stop()
        self.assertTrue(stop_finished.is_set())
        self.assertEqual(self.render("active")["status"], "completed")
        self.assertEqual(self.render("waiting")["status"], "queued")
        self.assertEqual(calls, ["active"])

    def test_unhandled_handler_failure_is_terminal_and_does_not_stall_queue(self):
        failed_job = self.add_render("broken")
        self.add_render("following")
        calls = []

        def handler(row, owner):
            calls.append(row["id"])
            if row["id"] == "broken":
                raise RuntimeError("Unexpected handler failure")
            with self.db() as c:
                c.execute(
                    "UPDATE renders SET status='completed' WHERE id=? AND worker_id=?",
                    (row["id"], owner),
                )

        queue = RenderQueue(self.db, handler, max_workers=1, poll_seconds=0.01)
        with self.assertLogs("studioflow.jobs", level="ERROR") as logged:
            queue.start()
            try:
                self.wait_for(lambda: self.render("following")["status"] == "completed")
            finally:
                queue.stop()
        self.assertTrue(
            any("Render handler failed for broken" in line for line in logged.output)
        )
        self.assertEqual(self.render("broken")["status"], "failed")
        self.assertEqual(self.job_status(failed_job), "finished")
        self.assertIn("Check server logs", self.render("broken")["error"])
        self.assertEqual(calls, ["broken", "following"])

    def test_handler_failure_cannot_overwrite_another_workers_result(self):
        self.add_render("transferred", status="running", worker="replacement")

        def handler(*_):
            raise RuntimeError("Late failure from old worker")

        queue = RenderQueue(self.db, handler)
        with self.assertLogs("studioflow.jobs", level="ERROR"):
            queue.execute(self.render("transferred"))
        row = self.render("transferred")
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["worker_id"], "replacement")
        self.assertEqual(row["error"], "")

    def test_failed_auxiliary_pass_is_consumed_once_and_later_pass_recovers(self):
        recovered = threading.Event()
        calls = []

        def auxiliary(owner):
            calls.append(owner)
            if len(calls) == 1:
                raise RuntimeError("Temporary transfer failure")
            recovered.set()

        queue = RenderQueue(
            self.db,
            lambda *_: None,
            auxiliary=auxiliary,
            lease_seconds=3,
            poll_seconds=0.01,
        )
        with self.assertLogs("studioflow.jobs", level="ERROR") as logged:
            queue.start()
            try:
                self.assertTrue(recovered.wait(timeout=5))
            finally:
                queue.stop()
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(len(logged.records), 1)
        self.assertIn("Background transfer/export pass failed", logged.output[0])
        self.assertIn("Temporary transfer failure", logged.output[0])

    def test_blocked_dispatcher_does_not_stop_lease_renewal(self):
        started, release, blocked, unblock = (threading.Event() for _ in range(4))
        self.add_render("active")

        def handler(row, owner):
            started.set()
            release.wait(timeout=10)

        queue = RenderQueue(
            self.db, handler, max_workers=1, lease_seconds=1, poll_seconds=0.01
        )
        real_recover = queue.recover

        def slow_recover():
            if started.is_set():
                blocked.set()
                unblock.wait(timeout=10)
            real_recover()

        queue.recover = slow_recover
        observer = RenderQueue(self.db, lambda *_: None, lease_seconds=1)
        queue.start()
        try:
            self.assertTrue(blocked.wait(timeout=5))
            time.sleep(1.2)
            observer.recover()
            self.assertEqual(self.render("active")["status"], "running")
        finally:
            unblock.set()
            release.set()
            queue.stop()


if __name__ == "__main__":
    unittest.main()
