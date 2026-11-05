"""Private immutable object storage. Local implementation is for offline previews."""

import hashlib
import base64
import os
import re
import shutil
import uuid
from pathlib import Path


def checksum(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_key(key):
    if not re.fullmatch(r"assets/[0-9a-f]{2}/[0-9a-f]{32}\.[a-z0-9]+", key):
        raise ValueError("Invalid immutable object key")
    return key


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, key):
        return self.root / validate_key(key)

    def put(self, key, source, mime, sha256):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if checksum(target) != sha256:
                raise ValueError("Immutable asset collision")
            return
        temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with Path(source).open("rb") as src, temporary.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                if checksum(target) != sha256:
                    raise ValueError("Immutable asset collision")
        finally:
            temporary.unlink(missing_ok=True)

    def open(self, key):
        return self.path(key).open("rb")

    def stat(self, key):
        path = self.path(key)
        return {"size": path.stat().st_size, "sha256": checksum(path)}

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)

    def health(self):
        self.root.mkdir(parents=True, exist_ok=True)

    def has_objects(self):
        return self.root.exists() and any(p.is_file() for p in self.root.rglob("*"))


class S3Store:
    def __init__(self, settings):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )

    def put(self, key, source, mime, sha256):
        from botocore.exceptions import ClientError

        validate_key(key)
        try:
            existing = self.stat(key)
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) not in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                raise
        else:
            if existing != {"size": Path(source).stat().st_size, "sha256": sha256}:
                raise ValueError("Immutable asset collision")
            return
        try:
            with Path(source).open("rb") as stream:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=stream,
                    ContentType=mime,
                    Metadata={"sha256": sha256},
                    IfNoneMatch="*",
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(
                        "ascii"
                    ),
                )
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) not in ("412", "PreconditionFailed"):
                raise
        if self.stat(key) != {"size": Path(source).stat().st_size, "sha256": sha256}:
            raise ValueError("Object verification failed")

    def open(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=validate_key(key))["Body"]

    def stat(self, key):
        value = self.client.head_object(Bucket=self.bucket, Key=validate_key(key))
        return {
            "size": value["ContentLength"],
            "sha256": value.get("Metadata", {}).get("sha256", ""),
        }

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=validate_key(key))

    def health(self):
        self.client.head_bucket(Bucket=self.bucket)

    def initialize(self):
        from botocore.exceptions import ClientError

        try:
            self.health()
        except ClientError as exc:
            if str(exc.response["Error"]["Code"]) not in (
                "404",
                "NoSuchBucket",
                "NotFound",
            ):
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def has_objects(self):
        return bool(
            self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=1).get("KeyCount")
        )
