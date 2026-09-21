# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Shared S3/MinIO client builder - same code path either way, only S3_ENDPOINT_URL differs."""

import os

import boto3


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
    )
