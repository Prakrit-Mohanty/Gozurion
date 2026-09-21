# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
Permanent vs transient failures, used for Temporal's non_retryable_error_types.

Temporal matches by exact exception class name (string equality, no MRO
walk), so every concrete exception is listed individually in each
activity's RetryPolicy - GozuError alone won't match.
"""


class GozuError(Exception):
    """Shared base for isinstance checks - not used for Temporal's matching."""


class ScannerAuthError(GozuError):
    """401/403 from the scanner backend - invalid/expired token."""


class TicketAuthError(GozuError):
    """401/403 from the ticket backend."""


class TicketValidationError(GozuError):
    """400 from the ticket backend - permanently malformed request."""
