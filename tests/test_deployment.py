"""Offline deployment failure checks; no production Docker daemon is accessed."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import deploy as deployment
from scripts import maintenance as platform_maintenance


REVISION = "a" * 40
RUNTIME_ENV = "APP_MODE=demo\nTEAM_USERS=offline:local-test-password\n"


class FakeDocker(deployment.Docker):
    image = "studioflow:" + REVISION

    def __init__(self, source, failure=None):
        self.events = []
        self.failure = failure
        self.network = "studioflow-platform"
        self.available_networks = [self.network]
        self.container = {
            "status": "running",
            "image": "old-image",
            "project": "studioflow",
            "mounts": [
                {"Type": "bind", "Source": str(source), "Destination": "/app/data"}
            ],
        }

    def current(self):
        return self.container

    def load(self, *args):
        self.events.append("load")

    def compose(self, release, data, project, *args, **kwargs):
        self.events.append(args[0])
        if args[0] == self.failure:
            raise RuntimeError("Simulated " + self.failure + " failure")
        if args[0] == "config":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "services": {
                            "studioflow": {
                                "environment": {
                                    "APP_MODE": "demo",
                                    "TEAM_USERS": "offline:local-test-password",
                                    "STUDIOFLOW_LOAD_DOTENV": "0",
                                    "STUDIOFLOW_REQUIRE_AUTH": "1",
                                }
                            }
                        },
                        "networks": {
                            "platform": {"name": self.network, "external": True}
                        },
                    }
                )
            )
        if args[0] == "up":
            self.container = {
                **self.container,
                "image": "new-image",
                "mounts": [
                    {"Type": "bind", "Source": str(data), "Destination": "/app/data"}
                ],
            }

    def command(self, *args, **kwargs):
        if args[:2] == ("network", "ls"):
            self.events.append("network-check")
            return SimpleNamespace(stdout="\n".join(self.available_networks))
        if args[0] == "run" and deployment.VALIDATE_RUNTIME in args:
            self.events.append("validate-settings")
            if self.failure == "run":
                raise RuntimeError("Simulated settings validation failure")
        return SimpleNamespace(stdout="new-image\n")

    def stop(self):
        self.events.append("stop")

    def resume(self):
        self.events.append("resume")

    def helper(self, code, source, *, target=None, readonly=True, stdout=None):
        if code == deployment.BACKUP:
            self.events.append("backup")
            if self.failure == "backup":
                raise RuntimeError("Simulated disk full")
            stdout.write(b"offline-backup")
        elif code == deployment.COPY_DATA:
            self.events.append("copy")
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif code == deployment.PERMISSIONS:
            self.events.append("permissions")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="studioflow-deploy-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / "permanent"
        self.state.mkdir()
        self.source = self.base / "old-checkout" / "data"
        self.source.mkdir(parents=True)
        (self.source / "studio.sqlite3").write_bytes(b"existing database")
        (self.source / "uploads").mkdir()
        (self.source / "uploads/product.png").write_bytes(b"existing photo")
        self.bundle = self.base / "artifact"
        self.bundle.mkdir()
        for name in ("image.tar.gz", "compose.yml", "deploy.py"):
            (self.bundle / name).write_bytes(b"fixture-" + name.encode())
        self.manifest = {
            "revision": REVISION,
            "files": {
                name: deployment.digest(self.bundle / name)
                for name in ("image.tar.gz", "compose.yml", "deploy.py")
            },
        }
        (self.bundle / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def run_deploy(self, docker, **kwargs):
        return deployment.deploy(
            self.bundle, self.state, REVISION, RUNTIME_ENV, docker, **kwargs
        )

    def test_first_deployment_backs_up_before_copy_and_preserves_original_data(self):
        docker = FakeDocker(self.source)
        release = self.run_deploy(docker)
        self.assertLess(docker.events.index("stop"), docker.events.index("backup"))
        self.assertLess(docker.events.index("backup"), docker.events.index("copy"))
        self.assertLess(docker.events.index("copy"), docker.events.index("up"))
        self.assertEqual(
            (self.source / "studio.sqlite3").read_bytes(), b"existing database"
        )
        self.assertEqual(
            (self.state / "data/studio.sqlite3").read_bytes(), b"existing database"
        )
        self.assertEqual(
            (self.state / "data/uploads/product.png").read_bytes(), b"existing photo"
        )
        self.assertEqual((self.state / ".env").read_text(), RUNTIME_ENV)
        self.assertEqual(
            json.loads((self.state / "current.json").read_text())["revision"], REVISION
        )
        self.assertTrue((release / "compose.yml").is_file())
        self.assertEqual(len(list((self.state / "backups").glob("*.tar.gz"))), 1)

    def test_existing_permanent_data_is_backed_up_without_a_second_migration(self):
        permanent = self.state / "data"
        shutil.copytree(self.source, permanent)
        docker = FakeDocker(permanent)
        self.run_deploy(docker)
        self.assertIn("backup", docker.events)
        self.assertNotIn("copy", docker.events)

    def test_conflicting_workspaces_are_never_merged_or_stopped(self):
        (self.state / "data").mkdir()
        (self.state / "data/new-work").write_bytes(b"new work")
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, "refusing to merge"):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])
        self.assertEqual((self.state / "data/new-work").read_bytes(), b"new work")

    def test_checksum_or_revision_mismatch_cannot_reach_docker(self):
        (self.bundle / "image.tar.gz").write_bytes(b"changed artifact")
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])
        with self.assertRaisesRegex(ValueError, "revision"):
            deployment.verify_bundle(self.bundle, "b" * 40)

    def test_missing_secret_or_invalid_runtime_settings_leave_existing_service_running(
        self,
    ):
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, "PROD_ENV_FILE"):
            deployment.deploy(self.bundle, self.state, REVISION, "", docker)
        self.assertEqual(docker.events, [])
        docker.failure = "run"
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertNotIn("stop", docker.events)

    def test_newer_main_revision_prevents_stopping_production(self):
        checks = iter([True, False])
        docker = FakeDocker(self.source)
        self.assertIsNone(self.run_deploy(docker, check_head=lambda: next(checks)))
        self.assertNotIn("stop", docker.events)

    def test_backup_failure_resumes_original_service_without_replacement(self):
        docker = FakeDocker(self.source, failure="backup")
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertIn("resume", docker.events)
        self.assertNotIn("up", docker.events)
        self.assertEqual(
            (self.source / "studio.sqlite3").read_bytes(), b"existing database"
        )

    def test_failed_new_healthcheck_never_restores_database_or_restarts_old_workers(
        self,
    ):
        docker = FakeDocker(self.source, failure="up")
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertNotIn("resume", docker.events)
        self.assertFalse((self.state / "current.json").exists())
        self.assertTrue((self.state / "data/studio.sqlite3").exists())
        self.assertEqual(len(list((self.state / "backups").glob("*.tar.gz"))), 1)

    def test_data_inside_the_checkout_or_temporary_directory_is_rejected(self):
        for location in (self.base / "checkout", self.base / "temp"):
            target = location / "studioflow"
            target.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "outside checkout"):
                deployment.validate_state_dir(
                    target, self.base / "checkout", self.base / "temp"
                )

    def test_backup_directory_cannot_be_inside_the_source_data(self):
        docker = FakeDocker(self.base)
        with self.assertRaisesRegex(ValueError, "contain its own"):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])

    def test_remote_docker_context_is_rejected_before_container_operations(self):
        with patch.dict(
            "os.environ",
            {"DOCKER_HOST": "tcp://remote.invalid:2376", "DOCKER_CONTEXT": ""},
        ):
            with self.assertRaisesRegex(ValueError, "local Linux Docker socket"):
                deployment.Docker("studioflow:test").current()

    def test_wrong_container_image_never_counts_as_a_successful_release(self):
        docker = FakeDocker(self.source)
        docker.command = lambda *args, **kwargs: SimpleNamespace(
            stdout="different-image\n"
        )
        with self.assertRaisesRegex(RuntimeError, "intended image"):
            self.run_deploy(docker)
        self.assertFalse((self.state / "current.json").exists())
        self.assertNotIn("resume", docker.events)

    def test_existing_writable_state_needs_no_privileged_preparation(self):
        docker = Mock()
        result = deployment.prepare_state_dir(self.state, self.bundle, REVISION, docker)
        self.assertEqual(result, self.state.resolve())
        self.assertEqual(docker.mock_calls, [])

    def test_missing_custom_directory_is_not_created_or_sent_to_docker(self):
        docker = Mock()
        target = self.base / "missing"
        with self.assertRaisesRegex(
            ValueError, "production location|supports /srv/studioflow only"
        ):
            deployment.prepare_state_dir(target, self.bundle, REVISION, docker)
        self.assertFalse(target.exists())
        self.assertEqual(docker.mock_calls, [])

    def prepare_missing_production_state(self, docker):
        state = Path("/srv/studioflow")
        with (
            patch.object(deployment, "validate_state_path", return_value=state),
            patch.object(deployment, "validate_state_dir", return_value=state),
            patch.object(Path, "is_dir", lambda path: path == Path("/srv")),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(os, "getuid", return_value=1234, create=True),
            patch.object(os, "getgid", return_value=5678, create=True),
        ):
            return deployment.prepare_state_dir(state, self.bundle, REVISION, docker)

    def test_bootstrap_verifies_bundle_and_image_before_preparing_fixed_directory(self):
        docker = Mock(image="studioflow:" + REVISION)
        self.prepare_missing_production_state(docker)
        self.assertEqual(
            [call[0] for call in docker.method_calls], ["current", "load", "command"]
        )
        args = docker.command.call_args.args
        self.assertEqual(
            args[:7],
            ("run", "--rm", "--network", "none", "--read-only", "--user", "0:0"),
        )
        self.assertIn("type=bind,source=/srv,target=/host-srv", args)
        self.assertEqual(
            args[-4:], (deployment.PREPARE_STATE, "/host-srv", "1234", "5678")
        )

    def test_bootstrap_rejects_tampered_bundle_before_any_docker_operation(self):
        (self.bundle / "deploy.py").write_bytes(b"tampered")
        docker = Mock()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare_missing_production_state(docker)
        self.assertEqual(docker.mock_calls, [])

    def test_bootstrap_failure_cannot_stop_existing_container(self):
        docker = Mock(image="studioflow:" + REVISION)
        docker.command.side_effect = RuntimeError("Simulated Docker permission failure")
        with self.assertRaisesRegex(RuntimeError, "permission failure"):
            self.prepare_missing_production_state(docker)
        docker.stop.assert_not_called()

    def platform_bundle(self):
        self.manifest["format"] = 2
        for name in (
            "operations.yml",
            "infrastructure.yml",
            "infrastructure.production.yml",
            "maintenance.py",
        ):
            (self.bundle / name).write_bytes(b"fixture-" + name.encode())
            self.manifest["files"][name] = deployment.digest(self.bundle / name)
        (self.bundle / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def test_platform_schema_guard_fails_before_stopping_the_old_sqlite_service(self):
        self.platform_bundle()
        docker = PlatformDocker(self.source, failure="preflight")
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            self.run_deploy(docker)
        self.assertNotIn("stop", docker.events)
        self.assertNotIn("external-backup", docker.events)
        self.assertEqual(
            (self.source / "studio.sqlite3").read_bytes(), b"existing database"
        )

    def test_missing_platform_network_reports_prepare_before_starting_any_compose_service(
        self,
    ):
        self.platform_bundle()
        docker = PlatformDocker(self.source)
        docker.available_networks = ["bridge", "host", "none"]
        with self.assertRaisesRegex(RuntimeError, "action=prepare") as failure:
            self.run_deploy(docker)
        self.assertIn("studioflow-platform", str(failure.exception))
        self.assertIn("production-maintenance", str(failure.exception))
        self.assertEqual(
            docker.events, ["load", "config", "validate-settings", "network-check"]
        )
        self.assertEqual(
            (self.source / "studio.sqlite3").read_bytes(), b"existing database"
        )
        self.assertFalse((self.state / "current.json").exists())

    def test_custom_platform_network_uses_resolved_compose_name(self):
        self.platform_bundle()
        docker = PlatformDocker(self.source)
        docker.network = "studioflow-custom-production"
        docker.available_networks = [docker.network]
        self.run_deploy(docker)
        self.assertLess(
            docker.events.index("validate-settings"),
            docker.events.index("network-check"),
        )
        self.assertLess(
            docker.events.index("network-check"), docker.events.index("preflight")
        )
        self.assertLess(docker.events.index("preflight"), docker.events.index("stop"))

    def maintenance_action(self, action, docker):
        self.platform_bundle()
        with (
            patch.object(sys, "platform", "linux"),
            patch.dict(
                sys.modules,
                {"fcntl": SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)},
            ),
            patch.object(
                sys,
                "argv",
                [
                    "maintenance",
                    "--bundle",
                    str(self.bundle),
                    "--revision",
                    REVISION,
                    "--action",
                    action,
                ],
            ),
            patch.dict(os.environ, {"PROD_ENV_FILE": RUNTIME_ENV}),
            patch.object(platform_maintenance, "Docker", return_value=docker),
            patch.object(
                platform_maintenance, "prepare_state_dir", return_value=self.state
            ),
            patch.object(platform_maintenance.os, "umask"),
        ):
            platform_maintenance.main()

    def test_explicit_prepare_can_create_infrastructure_without_an_existing_network(
        self,
    ):
        docker = Mock(spec=deployment.Docker)
        docker.command.return_value = SimpleNamespace(
            stdout=json.dumps(
                {
                    "services": {"ops": {"environment": {}}},
                    "networks": {
                        "platform": {"name": "studioflow-platform", "external": True}
                    },
                }
            )
        )
        self.maintenance_action("prepare", docker)
        docker.validate_runtime.assert_called_once()
        docker.require_platform_network.assert_not_called()
        docker.helper.assert_called_once()
        commands = [call.args for call in docker.command.call_args_list]
        self.assertTrue(
            any(
                "studioflow-infrastructure" in args and "up" in args
                for args in commands
            )
        )
        self.assertTrue(any(args[-2:] == ("ops", "init") for args in commands))
        docker.stop.assert_not_called()

    def test_prepare_with_invalid_settings_cannot_start_infrastructure_or_stop_app(
        self,
    ):
        docker = Mock(spec=deployment.Docker)
        docker.command.return_value = SimpleNamespace(
            stdout='{"services":{"ops":{"environment":{}}}}'
        )
        docker.validate_runtime.side_effect = RuntimeError(
            "Incomplete platform settings"
        )
        with self.assertRaisesRegex(RuntimeError, "Incomplete platform settings"):
            self.maintenance_action("prepare", docker)
        docker.helper.assert_not_called()
        self.assertEqual(docker.command.call_count, 1)  # Only Compose configuration.
        docker.stop.assert_not_called()

    def test_maintenance_backup_missing_network_reports_setup_before_launching_ops(
        self,
    ):
        docker = Mock(spec=deployment.Docker)
        docker.command.return_value = SimpleNamespace(
            stdout='{"services":{"ops":{"environment":{}}}}'
        )
        docker.require_platform_network.side_effect = RuntimeError(
            "Network missing; action=prepare"
        )
        with self.assertRaisesRegex(RuntimeError, "action=prepare"):
            self.maintenance_action("backup", docker)
        self.assertEqual(docker.command.call_count, 1)
        docker.helper.assert_not_called()
        docker.stop.assert_not_called()

    def test_network_daemon_failure_cannot_stop_or_replace_running_app(self):
        self.platform_bundle()
        docker = PlatformDocker(self.source)
        command = docker.command

        def unavailable(*args, **kwargs):
            if args[:2] == ("network", "ls"):
                raise subprocess.CalledProcessError(1, ["docker", "network", "ls"])
            return command(*args, **kwargs)

        docker.command = unavailable
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_deploy(docker)
        self.assertNotIn("stop", docker.events)
        self.assertNotIn("run", docker.events)
        self.assertNotIn("up", docker.events)

    def test_platform_external_backup_failure_leaves_the_running_service_in_place(self):
        self.platform_bundle()
        docker = PlatformDocker(self.source, failure="external-backup")
        with self.assertRaisesRegex(RuntimeError, "external-backup"):
            self.run_deploy(docker)
        self.assertIn("preflight", docker.events)
        self.assertNotIn("stop", docker.events)
        self.assertNotIn("up", docker.events)

    def test_platform_deployment_uses_migrated_data_and_starts_backup_service(self):
        self.platform_bundle()
        (self.state / "data").mkdir()
        (self.state / "data" / "migrated-marker").write_text("preserve-new-data")
        docker = PlatformDocker(self.source)
        self.run_deploy(docker)
        self.assertLess(
            docker.events.index("preflight"), docker.events.index("external-backup")
        )
        self.assertLess(
            docker.events.index("external-backup"), docker.events.index("stop")
        )
        self.assertLess(
            docker.events.index("up"), docker.events.index("backup-service")
        )
        self.assertNotIn("copy", docker.events)
        self.assertFalse((self.state / "data" / "studio.sqlite3").exists())
        self.assertEqual(
            (self.state / "data" / "migrated-marker").read_text(), "preserve-new-data"
        )


class PlatformDocker(FakeDocker):
    def compose(self, release, data, project, *args, **kwargs):
        if "preflight" in args:
            self.events.append("preflight")
            if self.failure == "preflight":
                raise RuntimeError("Simulated preflight failure")
        return super().compose(release, data, project, *args, **kwargs)

    def command(self, *args, **kwargs):
        if "backup" in args:
            self.events.append("external-backup")
            if self.failure == "external-backup":
                raise RuntimeError("Simulated external-backup failure")
        if "backups" in args:
            self.events.append("backup-service")
        return super().command(*args, **kwargs)


class RuntimeValidationTests(unittest.TestCase):
    def test_settings_check_needs_no_platform_network_and_preserves_resolved_secret_values(
        self,
    ):
        docker = deployment.Docker("studioflow:" + REVISION)
        environment = {
            "TEAM_USERS": 'owner:quoted "$private" password',
            "STUDIOFLOW_LOAD_DOTENV": "0",
            "STUDIOFLOW_REQUIRE_AUTH": "1",
            "S3_SECRET_ACCESS_KEY": "a-secret-not-for-logs",
            "UNSET": None,
        }
        config = {"services": {"studioflow": {"environment": environment}}}
        with patch.object(docker, "command") as command:
            docker.validate_runtime(config, platform=True)
        args, kwargs = command.call_args
        self.assertEqual(
            args[:7],
            (
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--interactive",
                "--entrypoint",
            ),
        )
        self.assertNotIn("--mount", args)
        self.assertNotIn("compose", args)
        self.assertNotIn("a-secret-not-for-logs", str(args))
        self.assertEqual(
            json.loads(kwargs["input"]),
            {k: v for k, v in environment.items() if v is not None},
        )
        self.assertEqual(args[-1], "platform")

    def test_maintenance_settings_check_uses_the_operations_service(self):
        docker = deployment.Docker("studioflow:" + REVISION)
        config = {"services": {"ops": {"environment": {"APP_MODE": "demo"}}}}
        with patch.object(docker, "command") as command:
            docker.validate_runtime(config, platform=True, service="ops")
        self.assertEqual(
            json.loads(command.call_args.kwargs["input"]), {"APP_MODE": "demo"}
        )

    def test_platform_settings_reject_legacy_configuration_without_initializing_any_data(
        self,
    ):
        from studioflow.settings import Settings
        import io

        for complete in (False, True):
            environment = {
                "APP_MODE": "demo",
                "TEAM_USERS": "owner:test-password",
                "STUDIOFLOW_LOAD_DOTENV": "0",
                "STUDIOFLOW_REQUIRE_AUTH": "1",
            }
            if complete:
                environment.update(
                    DATABASE_URL="postgresql+psycopg://test:test@postgres:5432/studioflow",
                    STUDIOFLOW_STORAGE="s3",
                    S3_ENDPOINT="http://seaweedfs:8333",
                    S3_ACCESS_KEY_ID="test-key",
                    S3_SECRET_ACCESS_KEY="test-secret",
                )
            with (
                self.subTest(complete=complete),
                patch.dict(os.environ, {}, clear=True),
                patch.object(sys, "stdin", io.StringIO(json.dumps(environment))),
                patch.object(sys, "argv", ["-c", "platform"]),
                patch.object(Settings, "load", wraps=Settings.load) as load,
                patch("builtins.print"),
                patch.object(
                    Path,
                    "mkdir",
                    side_effect=AssertionError("Settings check must not write data"),
                ),
            ):
                if complete:
                    exec(
                        compile(deployment.VALIDATE_RUNTIME, "<runtime-check>", "exec"),
                        {},
                    )
                else:
                    with self.assertRaisesRegex(
                        SystemExit, "DATABASE_URL.*action=prepare"
                    ):
                        exec(
                            compile(
                                deployment.VALIDATE_RUNTIME, "<runtime-check>", "exec"
                            ),
                            {},
                        )
                load.assert_called_once()


@unittest.skipUnless(os.name == "posix", "Linux directory-descriptor checks run in CI")
class StateDirectoryHelperTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="studioflow-state-test-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.state = self.parent / "studioflow"

    def prepare(self):
        with patch.object(
            sys,
            "argv",
            ["prepare", str(self.parent), str(os.getuid()), str(os.getgid())],
        ):
            exec(compile(deployment.PREPARE_STATE, "<prepare-state>", "exec"), {})

    def test_creates_private_directory_owned_by_runner(self):
        self.prepare()
        info = self.state.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        self.assertEqual((info.st_uid, info.st_gid), (os.getuid(), os.getgid()))

    def test_repeated_preparation_preserves_workspace_contents_and_child_permissions(
        self,
    ):
        self.state.mkdir(mode=0o755)
        data = self.state / "studio.sqlite3"
        data.write_bytes(b"existing workspace")
        data.chmod(0o640)
        before = data.stat()
        self.prepare()
        self.prepare()
        self.assertEqual(data.read_bytes(), b"existing workspace")
        self.assertEqual(
            (data.stat().st_mode, data.stat().st_uid, data.stat().st_gid),
            (before.st_mode, before.st_uid, before.st_gid),
        )
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_rejects_directory_symlink_without_changing_target(self):
        outside = self.parent / "outside"
        outside.mkdir(mode=0o755)
        self.state.symlink_to(outside, target_is_directory=True)
        before = outside.stat()
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(outside.stat().st_mode, before.st_mode)

    def test_rejects_directory_owned_by_an_unrelated_account(self):
        self.state.mkdir()
        with (
            patch.object(
                os, "fstat", return_value=SimpleNamespace(st_uid=os.getuid() + 1)
            ),
            patch.object(os, "fchown") as change_owner,
        ):
            with self.assertRaisesRegex(RuntimeError, "another account"):
                self.prepare()
        change_owner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
