# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Generic report-storage interface - the scanner/agent talk to this, never a specific backend."""

from abc import ABC, abstractmethod


class ReportStorage(ABC):
    @abstractmethod
    def upload(self, bucket: str, key: str, body: bytes, content_type: str = "application/json") -> None:
        raise NotImplementedError

    @abstractmethod
    def download(self, bucket: str, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        """Every key under `prefix` - used for multi-repo discovery (src/tools/tools.py's
        discover_new_reports), not just single-key fetches."""
        raise NotImplementedError

    @abstractmethod
    def exists(self, bucket: str, key: str) -> bool:
        raise NotImplementedError
