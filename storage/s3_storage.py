# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""S3-protocol implementation - identical code against real AWS S3 or any S3-compatible store (MinIO, etc); only `endpoint_url` differs."""

import boto3

from storage.base import ReportStorage


class S3Storage(ReportStorage):
    def __init__(self, endpoint_url: str | None, access_key_id: str | None, secret_access_key: str | None):
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def upload(self, bucket: str, key: str, body: bytes, content_type: str = "application/json") -> None:
        self._client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)

    def download(self, bucket: str, key: str) -> bytes:
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()
