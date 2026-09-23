# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Builds a ReportStorage from explicit credentials, or from the environment - same shape as ticket/factory.py."""

import os

from storage.base import ReportStorage
from storage.s3_storage import S3Storage


def build_storage_client(backend: str, credentials: dict[str, str]) -> ReportStorage:
    """Pure constructor - no env reads. This is what a future per-repo/per-config DB row's backend+credentials would go through."""
    if backend != "s3":
        raise ValueError(f"Unrecognized STORAGE_BACKEND '{backend}'. Expected 's3'.")

    return S3Storage(
        access_key_id=credentials.get("s3_access_key_id"),
        secret_access_key=credentials.get("s3_secret_access_key"),
    )


def get_storage_client() -> ReportStorage:
    """Reads STORAGE_BACKEND/S3_* from the environment - always real AWS S3."""
    backend = os.environ.get("STORAGE_BACKEND", "s3")
    credentials = {
        "s3_access_key_id": os.environ.get("S3_ACCESS_KEY_ID", ""),
        "s3_secret_access_key": os.environ.get("S3_SECRET_ACCESS_KEY", ""),
    }
    return build_storage_client(backend, credentials)
