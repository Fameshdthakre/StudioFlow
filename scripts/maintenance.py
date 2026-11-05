"""Explicit first cutover on mvserver; normal push deployment cannot perform migration."""

import argparse
import os
import shutil
import sys
from pathlib import Path

if __package__:
    from .deploy import Docker, prepare_state_dir, private_file, verify_bundle, deploy
else:
    from deploy import Docker, prepare_state_dir, private_file, verify_bundle, deploy

PREPARE = """import os,pathlib
base=pathlib.Path('/workspace')
for name,uid in [('postgres',0),('objects',10002),('data',10001),('migration',10001),('secrets',10001)]:
    path=base/name
    if path.is_symlink(): raise RuntimeError('Refusing symbolic deployment path')
    path.mkdir(mode=0o750,exist_ok=True)
    os.chown(path,uid,uid)
    os.chmod(path,0o750)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--action",
        choices=["prepare", "migrate", "backup", "activate", "resume", "status"],
        required=True,
    )
    args = parser.parse_args()
    if sys.platform != "linux":
        raise RuntimeError("This workflow runs only on the Linux mvserver host")
    import fcntl

    os.umask(0o077)
    bundle = args.bundle.resolve()
    verify_bundle(bundle, args.revision)
    docker = Docker("studioflow:" + args.revision)
    state = prepare_state_dir(
        Path("/srv/studioflow"),
        bundle,
        args.revision,
        docker,
        os.environ.get("GITHUB_WORKSPACE"),
        os.environ.get("RUNNER_TEMP"),
    )
    with (state / ".deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        docker.load(bundle, args.revision)
        runtime_env = os.environ.get("PROD_ENV_FILE", "")
        if not runtime_env.strip():
            raise RuntimeError("Set the complete platform PROD_ENV_FILE secret")
        release = state / "platform-releases" / (args.revision + "-" + args.action)
        release.mkdir(parents=True, exist_ok=True, mode=0o700)
        env_file = release / "runtime.env"
        if env_file.exists():
            if env_file.read_text() != runtime_env.rstrip() + "\n":
                raise RuntimeError(
                    "This maintenance step was already staged with different settings; use a new tested revision"
                )
        else:
            private_file(env_file, runtime_env.rstrip() + "\n")
        for name in (
            "infrastructure.yml",
            "infrastructure.production.yml",
            "operations.yml",
        ):
            shutil.copyfile(bundle / name, release / name)
        env = dict(
            os.environ,
            STUDIOFLOW_OPS_IMAGE="studioflow-ops:" + args.revision,
            STUDIOFLOW_ENV_FILE=str(env_file),
        )

        def ops(*commands):
            docker.command(
                "compose",
                "--project-name",
                "studioflow-operations",
                "--env-file",
                env_file,
                "-f",
                release / "operations.yml",
                "run",
                "--rm",
                "ops",
                *commands,
                env=env,
            )

        if args.action == "prepare":
            docker.helper(PREPARE, state, readonly=False)
            docker.command(
                "compose",
                "--project-name",
                "studioflow-infrastructure",
                "--env-file",
                env_file,
                "-f",
                release / "infrastructure.yml",
                "-f",
                release / "infrastructure.production.yml",
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
                env=env,
            )
            ops("init")
            print(
                "Infrastructure initialized. The existing application has not been stopped."
            )
        elif args.action == "migrate":
            ops("preflight", "--local-test")
            current = docker.current()
            if not current:
                raise RuntimeError(
                    "Expected the original application container; use the documented fresh-install procedure otherwise"
                )
            mounts = [
                m
                for m in current["mounts"]
                if m["Destination"] == "/app/data" and m["Type"] == "bind"
            ]
            if len(mounts) != 1:
                raise RuntimeError(
                    "Original workspace bind mount could not be identified"
                )
            source = Path(mounts[0]["Source"]).resolve()
            if not (source / "studio.sqlite3").is_file():
                raise RuntimeError(
                    "No legacy SQLite workspace exists at the source mount"
                )
            destination = state / "migration" / ("final-" + args.revision)
            if destination.exists() and current["status"] == "running":
                raise RuntimeError(
                    "Original application resumed after this snapshot. Rehearse a new migration into an empty stack."
                )
            docker.stop()
            if not destination.exists():
                code = (
                    "from studioflow.operations.migration import snapshot; import json; r=snapshot('/workspace','/target/final-' + "
                    + repr(args.revision)
                    + "); print(json.dumps({k:v for k,v in r.items() if k!='files'})); assert not r['conflicts'], 'Migration audit conflicts'"
                )
                docker.helper(code, source, target=state / "migration")
                docker.helper(
                    "import os\nfor d,dirs,files in os.walk('/workspace'):\n os.chown(d,10001,10001)\n for f in files: os.chown(os.path.join(d,f),10001,10001)",
                    destination,
                    readonly=False,
                )
            ops("import", "--source", "/migration/final-" + args.revision)
            print(
                "Final import reconciled. Application remains stopped; perform external backup and fresh-stack restore before activation."
            )
        elif args.action == "backup":
            ops("backup")
        elif args.action == "activate":
            ops("activate", "--proof", "/migration/restore-verification.json")
        elif args.action == "resume":
            ops("preflight")
            deploy(bundle, state, args.revision, runtime_env, docker)
            ops("resume")
            print(
                "PostgreSQL application is accepting work. Preserve the original SQLite source; never automatically restore it over new writes."
            )
        else:
            ops("preflight", "--local-test")


if __name__ == "__main__":
    main()
