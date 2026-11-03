"""Package only the tested image and deploy inputs; never archive the checkout."""
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package(revision, destination):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected a full Git commit SHA')
    root = Path(__file__).resolve().parents[1]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / 'image.tar.gz').open('wb') as output, gzip.GzipFile(fileobj=output, mode='wb', compresslevel=1) as compressed:
        process = subprocess.Popen(['docker', 'image', 'save', 'studioflow:' + revision], stdout=subprocess.PIPE)
        try:
            shutil.copyfileobj(process.stdout, compressed)
        finally:
            process.stdout.close()
        if process.wait() != 0:
            raise RuntimeError('Could not export the tested image')
    shutil.copyfile(root / 'deploy/compose.production.yml', destination / 'compose.yml')
    shutil.copyfile(root / 'scripts/deploy.py', destination / 'deploy.py')
    files = {name: digest(destination / name) for name in ('image.tar.gz', 'compose.yml', 'deploy.py')}
    (destination / 'manifest.json').write_text(json.dumps({'revision': revision, 'files': files}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    package(sys.argv[1], sys.argv[2])
