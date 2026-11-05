"""CI readiness probe using only disposable test credentials."""

import sys
import time
import boto3
from botocore.config import Config

client = boto3.client(
    "s3",
    endpoint_url=sys.argv[1],
    aws_access_key_id="test-access",
    aws_secret_access_key="test-secret-long-enough",
    region_name="us-east-1",
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
)
deadline = time.monotonic() + 90
while True:
    try:
        client.list_buckets()
        break
    except Exception:
        if time.monotonic() > deadline:
            raise
        time.sleep(1)
print("Private test storage is ready")
