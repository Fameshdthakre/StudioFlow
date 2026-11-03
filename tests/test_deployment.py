"""Offline deployment failure checks; no production Docker daemon is accessed."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import deploy as deployment


REVISION = 'a' * 40
RUNTIME_ENV = 'APP_MODE=demo\nTEAM_USERS=offline:local-test-password\n'


class FakeDocker:
    image = 'studioflow:' + REVISION

    def __init__(self, source, failure=None):
        self.events = []
        self.failure = failure
        self.container = {'status': 'running', 'image': 'old-image', 'project': 'studioflow',
                          'mounts': [{'Type': 'bind', 'Source': str(source), 'Destination': '/app/data'}]}

    def current(self):
        return self.container

    def load(self, *args):
        self.events.append('load')

    def compose(self, release, data, project, *args):
        self.events.append(args[0])
        if args[0] == self.failure:
            raise RuntimeError('Simulated ' + self.failure + ' failure')
        if args[0] == 'up':
            self.container = {**self.container, 'image': 'new-image', 'mounts': [
                {'Type': 'bind', 'Source': str(data), 'Destination': '/app/data'}]}

    def command(self, *args, **kwargs):
        return SimpleNamespace(stdout='new-image\n')

    def stop(self):
        self.events.append('stop')

    def resume(self):
        self.events.append('resume')

    def helper(self, code, source, *, target=None, readonly=True, stdout=None):
        if code == deployment.BACKUP:
            self.events.append('backup')
            if self.failure == 'backup':
                raise RuntimeError('Simulated disk full')
            stdout.write(b'offline-backup')
        elif code == deployment.COPY_DATA:
            self.events.append('copy')
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif code == deployment.PERMISSIONS:
            self.events.append('permissions')


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='studioflow-deploy-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.state = self.base / 'permanent'
        self.state.mkdir()
        self.source = self.base / 'old-checkout' / 'data'
        self.source.mkdir(parents=True)
        (self.source / 'studio.sqlite3').write_bytes(b'existing database')
        (self.source / 'uploads').mkdir()
        (self.source / 'uploads/product.png').write_bytes(b'existing photo')
        self.bundle = self.base / 'artifact'
        self.bundle.mkdir()
        for name in ('image.tar.gz', 'compose.yml', 'deploy.py'):
            (self.bundle / name).write_bytes(b'fixture-' + name.encode())
        self.manifest = {'revision': REVISION, 'files': {name: deployment.digest(self.bundle / name)
            for name in ('image.tar.gz', 'compose.yml', 'deploy.py')}}
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def run_deploy(self, docker, **kwargs):
        return deployment.deploy(self.bundle, self.state, REVISION, RUNTIME_ENV, docker, **kwargs)

    def test_first_deployment_backs_up_before_copy_and_preserves_original_data(self):
        docker = FakeDocker(self.source)
        release = self.run_deploy(docker)
        self.assertLess(docker.events.index('stop'), docker.events.index('backup'))
        self.assertLess(docker.events.index('backup'), docker.events.index('copy'))
        self.assertLess(docker.events.index('copy'), docker.events.index('up'))
        self.assertEqual((self.source / 'studio.sqlite3').read_bytes(), b'existing database')
        self.assertEqual((self.state / 'data/studio.sqlite3').read_bytes(), b'existing database')
        self.assertEqual((self.state / 'data/uploads/product.png').read_bytes(), b'existing photo')
        self.assertEqual((self.state / '.env').read_text(), RUNTIME_ENV)
        self.assertEqual(json.loads((self.state / 'current.json').read_text())['revision'], REVISION)
        self.assertTrue((release / 'compose.yml').is_file())
        self.assertEqual(len(list((self.state / 'backups').glob('*.tar.gz'))), 1)

    def test_existing_permanent_data_is_backed_up_without_a_second_migration(self):
        permanent = self.state / 'data'
        shutil.copytree(self.source, permanent)
        docker = FakeDocker(permanent)
        self.run_deploy(docker)
        self.assertIn('backup', docker.events)
        self.assertNotIn('copy', docker.events)

    def test_conflicting_workspaces_are_never_merged_or_stopped(self):
        (self.state / 'data').mkdir()
        (self.state / 'data/new-work').write_bytes(b'new work')
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, 'refusing to merge'):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])
        self.assertEqual((self.state / 'data/new-work').read_bytes(), b'new work')

    def test_checksum_or_revision_mismatch_cannot_reach_docker(self):
        (self.bundle / 'image.tar.gz').write_bytes(b'changed artifact')
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])
        with self.assertRaisesRegex(ValueError, 'revision'):
            deployment.verify_bundle(self.bundle, 'b' * 40)

    def test_missing_secret_or_invalid_runtime_settings_leave_existing_service_running(self):
        docker = FakeDocker(self.source)
        with self.assertRaisesRegex(ValueError, 'PROD_ENV_FILE'):
            deployment.deploy(self.bundle, self.state, REVISION, '', docker)
        self.assertEqual(docker.events, [])
        docker.failure = 'run'
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertNotIn('stop', docker.events)

    def test_newer_main_revision_prevents_stopping_production(self):
        checks = iter([True, False])
        docker = FakeDocker(self.source)
        self.assertIsNone(self.run_deploy(docker, check_head=lambda: next(checks)))
        self.assertNotIn('stop', docker.events)

    def test_backup_failure_resumes_original_service_without_replacement(self):
        docker = FakeDocker(self.source, failure='backup')
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertIn('resume', docker.events)
        self.assertNotIn('up', docker.events)
        self.assertEqual((self.source / 'studio.sqlite3').read_bytes(), b'existing database')

    def test_failed_new_healthcheck_never_restores_database_or_restarts_old_workers(self):
        docker = FakeDocker(self.source, failure='up')
        with self.assertRaises(RuntimeError):
            self.run_deploy(docker)
        self.assertNotIn('resume', docker.events)
        self.assertFalse((self.state / 'current.json').exists())
        self.assertTrue((self.state / 'data/studio.sqlite3').exists())
        self.assertEqual(len(list((self.state / 'backups').glob('*.tar.gz'))), 1)

    def test_data_inside_the_checkout_or_temporary_directory_is_rejected(self):
        for location in (self.base / 'checkout', self.base / 'temp'):
            target = location / 'studioflow'
            target.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'outside checkout'):
                deployment.validate_state_dir(target, self.base / 'checkout', self.base / 'temp')

    def test_backup_directory_cannot_be_inside_the_source_data(self):
        docker = FakeDocker(self.base)
        with self.assertRaisesRegex(ValueError, 'contain its own'):
            self.run_deploy(docker)
        self.assertEqual(docker.events, [])

    def test_remote_docker_context_is_rejected_before_container_operations(self):
        with patch.dict('os.environ', {'DOCKER_HOST': 'tcp://remote.invalid:2376', 'DOCKER_CONTEXT': ''}):
            with self.assertRaisesRegex(ValueError, 'local Linux Docker socket'):
                deployment.Docker('studioflow:test').current()

    def test_wrong_container_image_never_counts_as_a_successful_release(self):
        docker = FakeDocker(self.source)
        docker.command = lambda *args, **kwargs: SimpleNamespace(stdout='different-image\n')
        with self.assertRaisesRegex(RuntimeError, 'intended image'):
            self.run_deploy(docker)
        self.assertFalse((self.state / 'current.json').exists())
        self.assertNotIn('resume', docker.events)


if __name__ == '__main__':
    unittest.main()
