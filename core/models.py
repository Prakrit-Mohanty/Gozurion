# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
# Editor: Prakrit Mohanty

"""Normalized domain vocabulary shared across scanner and ticket adapters."""

from enum import Enum

from pydantic import BaseModel


class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class Finding(BaseModel):
    """A single issue reported by a scanner, in tool-agnostic form."""

    key: str
    title: str
    severity: Severity
    component: str
    line: int | None
    message: str
    finding_type: str  # "vulnerability" or "hotspot"
    deep_link: str
    source_tool: str  # e.g. "sonarqube"
    branch: str | None = None
    commit_sha: str | None = None  # commit this finding was scanned at
    repo_full_name: str | None = None  # "{owner}/{repo}" on GitHub
    how_to_fix: str | None = None  # rule-level guidance, not fix-specific
    rule_key: str | None = None  # e.g. "python:S2068"
    llm_explanation: str | None = None  # LLM-generated explanation/fix
    code_snippet: str | None = None
    code_snippet_start_line: int | None = None


class CreatedTicket(BaseModel):
    finding_key: str
    ticket_key: str


class TicketResult(BaseModel):
    created: list[CreatedTicket] = []
    skipped: list[str] = []
    deferred: list[str] = []  # new but backlog-capped this run
    rollup_ticket: str | None = None  # shared ticket for `deferred`
    closed: list[str] = []  # auto-closed this run
