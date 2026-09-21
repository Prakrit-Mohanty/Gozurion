# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Generic ticketing interface every backend (Jira, later Linear/GitHub Issues) implements."""

import hashlib
from abc import ABC, abstractmethod

from core.models import Finding, Severity

SEVERITY_TO_PRIORITY = {
    Severity.CRITICAL: "Highest",
    Severity.HIGH: "High",
    Severity.MEDIUM: "Medium",
    Severity.LOW: "Low",
    Severity.INFO: "Low",
}
DEFAULT_PRIORITY = "Medium"

SUMMARY_MAX_LENGTH = 255

_IDENTITY_HASH_LENGTH = 20


def finding_identity(finding: Finding) -> str:
    """
    Branch-agnostic identity: same rule at the same file/line = same
    issue, regardless of which branch/scan reported it. Excludes
    finding.key/finding.branch on purpose - a scanner commonly stamps a
    different key per branch for the same underlying issue.
    """
    parts = [finding.component, str(finding.line), finding.rule_key or "", finding.finding_type]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:_IDENTITY_HASH_LENGTH]


class TicketClient(ABC):
    @abstractmethod
    def destination_id(self) -> str:
        """Stable id for where this client creates tickets, e.g. "jira:{base_url}:{project_key}"."""
        raise NotImplementedError

    @abstractmethod
    def find_existing(self, finding: Finding) -> str | None:
        """Existing ticket key for this finding's identity, if any - must key off finding_identity(), not finding.key."""
        raise NotImplementedError

    @abstractmethod
    def create_ticket(self, finding: Finding, custom_fields: dict[str, str] | None = None) -> str:
        """
        Create a ticket, return its key. `custom_fields` is optional and
        backend-specific. Bonus capabilities (attach_screenshot,
        add_comment, transition_to_done, upsert_rollup_ticket,
        ticket_exists, discover_custom_fields) are NOT part of this
        contract - callers use getattr(client, name, None) to detect them.
        """
        raise NotImplementedError
