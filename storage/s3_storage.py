# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""S3-protocol implementation - identical code against real AWS S3 or any S3-compatible store (MinIO, etc); only `endpoint_url` differs."""

import boto3
from botocore.config import Config

from storage.base import ReportStorage

# A region mismatch or unreachable network path can otherwise hang
# indefinitely rather than raise - boto3/urllib3 have no default ceiling
# of their own. Generous enough for a real upload/download, not so long a
# genuine outage silently eats an activity's whole retry budget.
_BOTO_CONFIG = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 3})


class S3Storage(ReportStorage):
    def __init__(self, endpoint_url: str | None, access_key_id: str | None, secret_access_key: str | None):
        # `or None` on all three - an empty string (unset in .env) must fall
        # through to boto3's own default credential chain (AWS_PROFILE/SSO,
        # instance role, ...), not get passed through literally as an
        # empty-string access key, which boto3 treats as a real (invalid)
        # credential rather than "none provided".
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            config=_BOTO_CONFIG,
        )

    def upload(self, bucket: str, key: str, body: bytes, content_type: str = "application/json") -> None:
        self._client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)

    def download(self, bucket: str, key: str) -> bytes:
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()
