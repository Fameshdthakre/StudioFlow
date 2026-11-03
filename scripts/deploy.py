"""Deploy a tested image on the local Linux Docker host, preserving workspace data.

No git checkout, image pruning, database restore or provider requests happen here.
The only first-deployment data migration copies the existing container's stopped
bind mount to an empty permanent directory, retaining the original and a backup.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


BACKUP = """import sys,tarfile
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
    archive.add('/workspace',arcname='data')
"""
COPY_DATA = """from pathlib import Path
import shutil
target=Path('/target')
if any(target.iterdir()):
    raise RuntimeError('Refusing to merge production workspaces')
shutil.copytree('/workspace',target,dirs_exist_ok=True,symlinks=True)
"""
PERMISSIONS = """import os
for directory,dirs,files in os.walk('/workspace',followlinks=False):
    for path,mode in [(directory,0o750)]+[(os.path.join(directory,f),0o640) for f in files]:
        if not os.path.islink(path):
            os.chown(path,10001,10001,follow_symlinks=False)
            os.chmod(path,mode,follow_symlinks=False)
"""


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_bundle(bundle, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected a full Git commit SHA')
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('revision') != revision or set(manifest.get('files', {})) != {'image.tar.gz', 'compose.yml', 'deploy.py'}:
        raise ValueError('Deployment bundle does not match this workflow revision')
    for name, expected in manifest['files'].items():
        if (bundle / name).is_symlink() or digest(bundle / name) != expected:
            raise ValueError('Deployment bundle checksum mismatch: ' + name)


def validate_state_dir(state, workspace=None, runner_temp=None):
    if not state.is_absolute() or state.is_symlink() or state.resolve() == Path(state.anchor):
        raise ValueError('Use an absolute, dedicated deployment directory')
    state = state.resolve()
    for location in (workspace, runner_temp):
        if location and state.is_relative_to(Path(location).resolve()):
            raise ValueError('Production data must be outside checkout and runner temporary directories')
    if not state.is_dir() or not os.access(state, os.W_OK):
        raise ValueError('Create /srv/studioflow with mode 700 and ownership matching the runner service account first')
    for name in ('data', 'releases', 'backups', '.env', 'current.json', '.deploy.lock'):
        if (state / name).is_symlink():
            raise ValueError('Deployment paths must not be symbolic links: ' + name)
    return state


def private_file(path, contents):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(contents)


def is_current_main(revision):
    repository = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository identity')
    request = urllib.request.Request('https://api.github.com/repos/' + repository + '/git/ref/heads/main',
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)['object']['sha'] == revision


class Docker:
    def __init__(self, image):
        self.image = image

    def command(self, *args, **kwargs):
        return subprocess.run(['docker', *map(str, args)], check=True, **kwargs)

    def current(self):
        host = os.environ.get('DOCKER_HOST') if not os.environ.get('DOCKER_CONTEXT') else None
        if not host:
            host = self.command('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}', capture_output=True, text=True).stdout.strip()
        if not host.startswith('unix://'):
            raise ValueError('Deployment requires the local Linux Docker socket, not a remote Docker context')
        # Never inspect .Config.Env: it includes the production API key.
        template = '{"status":{{json .State.Status}},"image":{{json .Image}},"mounts":{{json .Mounts}},"project":{{json (index .Config.Labels "com.docker.compose.project")}}}'
        result = subprocess.run(['docker', 'inspect', '--type', 'container', '--format', template, 'studioflow'], capture_output=True, text=True)
        if result.returncode:
            if 'no such' in result.stderr.lower():
                return None
            raise RuntimeError('Cannot inspect Docker; verify access to the local Docker daemon')
        return json.loads(result.stdout)

    def load(self, bundle, revision):
        self.command('image', 'load', '--input', bundle / 'image.tar.gz')
        result = self.command('image', 'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.revision"}}', self.image, capture_output=True, text=True)
        if result.stdout.strip() != revision:
            raise ValueError('Loaded image revision does not match the tested commit')

    def helper(self, code, source, *, target=None, readonly=True, stdout=None):
        for path in (source, target):
            if path and (',' in str(path) or '\n' in str(path)):
                raise ValueError('Unsupported Docker mount path')
        mount = f'type=bind,source={source},target=/workspace' + (',readonly' if readonly else '')
        args = ['run', '--rm', '--network', 'none', '--read-only', '--user', '0:0', '--mount', mount]
        if target:
            args += ['--mount', f'type=bind,source={target},target=/target']
        self.command(*args, '--entrypoint', 'python', self.image, '-c', code, stdout=stdout)

    def compose(self, release, data, project, *args):
        env = dict(os.environ, STUDIOFLOW_IMAGE=self.image, STUDIOFLOW_DATA_PATH=str(data),
                   STUDIOFLOW_ENV_FILE=str(release / 'runtime.env'))
        self.command('compose', '--project-name', project, '--env-file', release / 'runtime.env',
                     '-f', release / 'compose.yml', *args, env=env)

    def stop(self):
        self.command('stop', '--time', '240', 'studioflow')

    def resume(self):
        self.command('start', 'studioflow')


def deploy(bundle, state, revision, runtime_env, docker, check_head=lambda: True):
    verify_bundle(bundle, revision)
    if not check_head():
        print('Skipped: a newer revision is already on main.')
        return None
    current = docker.current()
    data, source, project = state / 'data', state / 'data', 'studioflow'
    if current:
        mounts = [mount for mount in current['mounts'] if mount['Destination'] == '/app/data']
        if len(mounts) != 1 or mounts[0]['Type'] != 'bind':
            raise ValueError('Existing container must have exactly one data bind mount; migrate other storage manually')
        source = Path(mounts[0]['Source']).resolve()
        if not source.is_dir() or state.is_relative_to(source):
            raise ValueError('Existing data mount is missing or would contain its own deployment backups')
        project = current.get('project') or ''
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
            raise ValueError('Existing studioflow container has no recognized Compose project')
        if source != data.resolve() and data.exists() and any(data.iterdir()):
            raise ValueError('Both old and permanent data directories contain files; refusing to merge or overwrite either')
    if not runtime_env.strip():
        existing_env = state / '.env'
        if not existing_env.is_file():
            raise ValueError('Set the production environment secret PROD_ENV_FILE before deploying')
        runtime_env = existing_env.read_text(encoding='utf-8')
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + revision[:12] + '-' + uuid.uuid4().hex[:6]
    release = state / 'releases' / tag
    release.mkdir(parents=True, mode=0o700)
    data.mkdir(mode=0o750, exist_ok=True)
    (state / 'backups').mkdir(mode=0o700, exist_ok=True)
    private_file(release / 'runtime.env', runtime_env.rstrip() + '\n')
    shutil.copyfile(bundle / 'compose.yml', release / 'compose.yml')
    shutil.copyfile(bundle / 'manifest.json', release / 'manifest.json')
    docker.load(bundle, revision)
    docker.compose(release, data, project, 'config', '--quiet')
    # Validate credentials/settings without importing app.py, starting workers,
    # applying migrations, or making any provider call.
    docker.compose(release, data, project, 'run', '--rm', '--no-deps', '--entrypoint', 'python', 'studioflow', '-c',
        "from pathlib import Path; from studioflow.settings import Settings; Settings.load(Path('/app')); print('Runtime settings validated')")
    if not check_head():
        print('Skipped before stopping production: a newer revision is on main.')
        return None
    backup = state / 'backups' / (tag + '.tar.gz')
    stopped = False
    replacing = False
    try:
        if current:
            docker.stop()
            stopped = True
        if source.is_dir():
            partial = backup.with_suffix('.partial')
            with partial.open('xb') as output:
                os.chmod(partial, 0o600)
                docker.helper(BACKUP, source, stdout=output)
                output.flush()
                os.fsync(output.fileno())
            partial.replace(backup)
            private_file(backup.with_suffix('.json'), json.dumps({'previous': current, 'source': str(source), 'revision': revision}, indent=2))
        if source != data.resolve():
            if not source.is_dir():
                raise ValueError('Existing data mount is missing on this host; refusing an empty replacement')
            docker.helper(COPY_DATA, source, target=data)
        docker.helper(PERMISSIONS, data, readonly=False)
        replacing = True
        docker.compose(release, data, project, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120', 'studioflow')
        # --wait uses the new container's image HEALTHCHECK, not an arbitrary
        # process that happens to answer on the host's port 8000.
        replacement = docker.current()
        expected_image = docker.command('image', 'inspect', '--format', '{{.Id}}', docker.image, capture_output=True, text=True).stdout.strip()
        if not replacement or replacement['image'] != expected_image:
            raise RuntimeError('Healthcheck did not verify the intended image')
        next_env = state / ('.env.' + uuid.uuid4().hex)
        private_file(next_env, runtime_env.rstrip() + '\n')
        next_env.replace(state / '.env')
        private_file(release / 'deployment.json', json.dumps({'revision': revision, 'image': expected_image, 'backup': str(backup), 'project': project}, indent=2))
        shutil.copyfile(release / 'deployment.json', state / 'current.json')
        print('Healthy deployment:', revision)
        print('Workspace backup:', backup)
        return release
    except BaseException:
        if stopped and not replacing and current['status'] == 'running':
            docker.resume()  # The original container/database were never replaced.
        if replacing:
            print('Deployment failed after replacement began. Preserve the new data and inspect the backup; no automatic database restore was attempted.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--check-main', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('Production deployment requires the Linux mvserver runner')
    import fcntl
    os.umask(0o077)
    state = validate_state_dir(args.state_dir, os.environ.get('GITHUB_WORKSPACE'), os.environ.get('RUNNER_TEMP'))
    with (state / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy(args.bundle.resolve(), state, args.revision, os.environ.get('PROD_ENV_FILE', ''),
               Docker('studioflow:' + args.revision),
               (lambda: is_current_main(args.revision)) if args.check_main else (lambda: True))


if __name__ == '__main__':
    main()
