# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
# Editor: Prakrit Mohanty

"""Jira implementation of TicketClient (see ticket/base.py for the contract)."""

from pathlib import Path
from typing import Any

import requests
from temporalio import activity

from core.errors import TicketAuthError, TicketValidationError
from core.models import Finding, Severity
from ticket.adf import bullet_list, code_block, doc, paragraph
from ticket.base import (
    DEFAULT_PRIORITY,
    SEVERITY_TO_PRIORITY,
    SUMMARY_MAX_LENGTH,
    TicketClient,
    finding_identity,
)
from ticket.jira_sprint import SprintAssigner

ROLLUP_LABEL = "gozu-backlog-rollup"
_ROLLUP_DESCRIPTION_MAX_LINES = 50

# find_existing_many() batches the dedup search into "labels in (...)"
# queries instead of one request per finding. Each label matches at most
# one issue (finding_identity() is 1:1 with a ticket), so a chunk's total
# possible matches is bounded by its own size - kept well under Jira's
# documented page-size cap so a single request per chunk is always
# complete, no cursor/pagination handling needed.
_DEDUP_BATCH_SIZE = 50

# Optional custom fields - a Jira admin creates these with these exact
# names for discover_custom_fields() to find them; otherwise the content
# stays in Description.
CUSTOM_FIELD_COMPONENT_NAME = "SonarQube Component"
CUSTOM_FIELD_LINE_NAME = "SonarQube Line"
CUSTOM_FIELD_SEVERITY_NAME = "SonarQube Severity"


def _normalize_label_value(value: str) -> str:
    """Jira labels can't contain spaces."""
    return value.strip().lower().replace(" ", "-")


def _resolve_api_base(site_url: str) -> str:
    """Site URL (e.g. https://x.atlassian.net) -> the api.atlassian.com/ex/jira/{cloudId}
    gateway URL that REST calls actually need to go through. Basic Auth against the plain
    site URL only works with a classic (unscoped) API token - Atlassian's newer API tokens
    with scopes ignore that route entirely and 401 with "Client must be authenticated",
    regardless of how the token/scopes are set up. Routing through the cloud-ID gateway
    instead works the same way for both token types, so this isn't conditional on which
    kind of token is in use. /_edge/tenant_info is a public, unauthenticated endpoint."""
    response = requests.get(f"{site_url}/_edge/tenant_info", timeout=10)
    if response.status_code != 200:
        raise RuntimeError(
            f"Couldn't resolve Jira cloud ID from {site_url}/_edge/tenant_info "
            f"(status {response.status_code}) - is JIRA_URL correct?"
        )
    return f"https://api.atlassian.com/ex/jira/{response.json()['cloudId']}"


_SCANNER_DISPLAY_NAMES = {
    "semgrep": "Semgrep",
    "trivy": "Trivy",
    "sonarqube": "SonarQube",
}


def _scanner_label(source_tool: str) -> str:
    """Display name for a finding's source_tool, e.g. "Semgrep" - falls back to a
    capitalized version of the raw value for any scanner not in the map above."""
    return _SCANNER_DISPLAY_NAMES.get(source_tool, source_tool.capitalize())


def _title_prefix(finding: Finding) -> str:
    """"Semgrep [org/repo]: " - deployed org-wide with every repo's tickets landing in
    one Jira project (see finding_identity()), both which scanner and which repo need
    to be visible without opening the ticket. Omits the repo bracket if unset (e.g. a
    manual local run scanner/git_context.py couldn't stamp)."""
    scanner = _scanner_label(finding.source_tool)
    return f"{scanner} [{finding.repo_full_name}]: " if finding.repo_full_name else f"{scanner}: "


class JiraClient(TicketClient):
    def __init__(self, base_url: str, email: str, api_token: str, project_key: str):
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        self.auth = (email, api_token)
        self.headers = {"Content-Type": "application/json"}
        # One TCP/TLS connection (keep-alive, pooled) reused for every call
        # this client makes, instead of a fresh handshake per request - a
        # single run can make dozens of these (one dedup search + one
        # create + sprint-add + remote-link per finding).
        self.session = requests.Session()
        # All REST calls go through the cloud-ID gateway, not self.base_url
        # directly - see _resolve_api_base().
        self.api_base = _resolve_api_base(self.base_url)
        self._sprints = SprintAssigner(self.session, self.api_base, self.auth, self.headers, project_key)

    def destination_id(self) -> str:
        return f"jira:{self.base_url}:{self.project_key}"

    def _raise_for_status(self, response: requests.Response, action: str) -> None:
        if 200 <= response.status_code < 300:
            return
        if response.status_code in (401, 403):
            raise TicketAuthError(f"Jira {action} failed with status {response.status_code} (invalid/expired token?): {response.text}")
        if response.status_code == 400:
            raise TicketValidationError(f"Jira {action} failed with status {response.status_code}: {response.text}")
        raise RuntimeError(f"Jira {action} failed with status {response.status_code}: {response.text}")

    def ticket_exists(self, ticket_key: str) -> bool:
        response = self.session.get(
            f"{self.api_base}/rest/api/3/issue/{ticket_key}",
            params={"fields": "key"},
            auth=self.auth,
            headers=self.headers,
        )
        if response.status_code == 404:
            return False
        self._raise_for_status(response, "get issue")
        return True

    def _find_by_label(self, label: str) -> str | None:
        jql = f'project = {self.project_key} AND labels = "{label}"'

        params: dict[str, Any] = {"jql": jql, "fields": "key", "maxResults": 1}
        response = self.session.get(
            f"{self.api_base}/rest/api/3/search/jql",
            params=params,
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "search")

        issues = response.json().get("issues", [])
        return issues[0]["key"] if issues else None

    def find_existing(self, finding: Finding) -> str | None:
        """Branch-agnostic dedupe - issue-{finding_identity(finding)} is stamped on every ticket at creation."""
        return self._find_by_label(f"issue-{finding_identity(finding)}")

    def _find_by_labels_batch(self, labels: list[str]) -> dict[str, str]:
        """label -> issue key, for whichever of `labels` already exist - one request per
        _DEDUP_BATCH_SIZE-sized chunk instead of one per label."""
        found: dict[str, str] = {}
        for i in range(0, len(labels), _DEDUP_BATCH_SIZE):
            chunk = labels[i : i + _DEDUP_BATCH_SIZE]
            quoted = ", ".join(f'"{label}"' for label in chunk)
            jql = f"project = {self.project_key} AND labels in ({quoted})"

            response = self.session.get(
                f"{self.api_base}/rest/api/3/search/jql",
                params={"jql": jql, "fields": "key,labels", "maxResults": len(chunk)},
                auth=self.auth,
                headers=self.headers,
            )
            self._raise_for_status(response, "batch search")

            wanted = set(chunk)
            for issue in response.json().get("issues", []):
                for label in issue.get("fields", {}).get("labels", []):
                    if label in wanted:
                        found[label] = issue["key"]
        return found

    def find_existing_many(self, findings: list[Finding]) -> dict[str, str]:
        """finding.key -> existing ticket key, for whichever of `findings` already have one -
        the batched equivalent of calling find_existing() once per finding. Bonus capability,
        not part of the TicketClient contract - callers detect it via getattr."""
        label_to_finding_key = {f"issue-{finding_identity(finding)}": finding.key for finding in findings}
        found_by_label = self._find_by_labels_batch(list(label_to_finding_key))
        return {label_to_finding_key[label]: ticket_key for label, ticket_key in found_by_label.items()}

    def _map_priority(self, severity: Severity) -> str:
        return SEVERITY_TO_PRIORITY.get(severity, DEFAULT_PRIORITY)

    def _build_summary(self, finding: Finding) -> str:
        summary = f"{_title_prefix(finding)}{finding.title}"
        if len(summary) > SUMMARY_MAX_LENGTH:
            summary = summary[: SUMMARY_MAX_LENGTH - 3] + "..."
        return summary

    def _build_labels(self, finding: Finding) -> list[str]:
        """issue-{identity} is the actual dedupe mechanism; branch-{branch} is informational only."""
        labels = [f"issue-{finding_identity(finding)}"]
        if finding.branch:
            labels.append(f"branch-{_normalize_label_value(finding.branch)}")
        return labels

    def _build_description(
        self,
        finding: Finding,
        component_moved: bool = False,
        line_moved: bool = False,
        severity_moved: bool = False,
    ) -> dict:
        """`*_moved` means that value is set as a real custom field instead, so it's dropped here to avoid duplication."""
        details = []
        if not component_moved:
            details.append(f"Component: {finding.component}")
        if not line_moved:
            details.append(f"Line: {finding.line}")
        details.append(f"Type: {finding.finding_type}")
        if not severity_moved:
            details.append(f"Severity: {finding.severity.value}")
        details += [
            f"Source: {finding.source_tool}",
            f"Branch: {finding.branch or 'unknown'}",
        ]
        content = [paragraph(finding.llm_explanation or finding.message), bullet_list(details)]
        if finding.how_to_fix:
            content.append(paragraph("How to fix:"))
            content.append(code_block(finding.how_to_fix))
        return doc(*content)

    def discover_custom_fields(self, names: list[str]) -> dict[str, str]:
        """name -> "customfield_XXXXX" for whichever of `names` exist in this Jira instance."""
        response = self.session.get(f"{self.api_base}/rest/api/3/field", auth=self.auth, headers=self.headers)
        self._raise_for_status(response, "list fields")
        wanted = set(names)
        return {field["name"]: field["id"] for field in response.json() if field.get("name") in wanted}

    def _build_create_payload(
        self,
        finding: Finding,
        component_field_id: str | None,
        line_field_id: str | None,
        severity_field_id: str | None = None,
    ) -> dict[str, Any]:
        line_value = finding.line if (line_field_id and finding.line is not None) else None
        fields: dict[str, Any] = {
            "project": {"key": self.project_key},
            "summary": self._build_summary(finding),
            "issuetype": {"name": "Bug"},
            "priority": {"name": self._map_priority(finding.severity)},
            "labels": self._build_labels(finding),
            "description": self._build_description(
                finding,
                component_moved=bool(component_field_id),
                line_moved=line_value is not None,
                severity_moved=bool(severity_field_id),
            ),
        }
        if component_field_id:
            fields[component_field_id] = finding.component
        if line_field_id and line_value is not None:
            fields[line_field_id] = line_value
        if severity_field_id:
            fields[severity_field_id] = finding.severity.value
        return {"fields": fields}

    def _rejected_custom_field_ids(self, response: requests.Response, candidate_ids: set[str]) -> set[str]:
        """Which of `candidate_ids` Jira's 400 response rejected - empty if the 400 is unrelated to custom fields."""
        try:
            body = response.json()
        except ValueError:
            return set()
        error_keys = set((body.get("errors") or {}).keys())
        if not error_keys or not error_keys <= candidate_ids:
            return set()
        return error_keys

    def _create_remote_link(self, issue_key: str, url: str) -> None:
        payload: dict[str, Any] = {"object": {"url": url, "title": "SonarQube finding"}}
        response = self.session.post(
            f"{self.api_base}/rest/api/3/issue/{issue_key}/remotelink",
            json=payload,
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "create remote link")

    def create_ticket(self, finding: Finding, custom_fields: dict[str, str] | None = None) -> str:
        """Create a Jira issue, return its key. Falls back to Description if custom_fields are rejected."""
        custom_fields = custom_fields or {}
        component_field_id = custom_fields.get(CUSTOM_FIELD_COMPONENT_NAME)
        line_field_id = custom_fields.get(CUSTOM_FIELD_LINE_NAME)
        severity_field_id = custom_fields.get(CUSTOM_FIELD_SEVERITY_NAME)

        payload = self._build_create_payload(finding, component_field_id, line_field_id, severity_field_id)
        response = self.session.post(
            f"{self.api_base}/rest/api/3/issue",
            json=payload,
            auth=self.auth,
            headers=self.headers,
        )

        if response.status_code == 400 and (component_field_id or line_field_id or severity_field_id):
            candidate_ids = {fid for fid in (component_field_id, line_field_id, severity_field_id) if fid}
            rejected = self._rejected_custom_field_ids(response, candidate_ids)
            if rejected:
                activity.logger.warning(
                    f"Jira rejected custom field(s) {sorted(rejected)} for finding {finding.key} - "
                    "retrying with that content folded back into Description"
                )
                if component_field_id in rejected:
                    component_field_id = None
                if line_field_id in rejected:
                    line_field_id = None
                if severity_field_id in rejected:
                    severity_field_id = None
                payload = self._build_create_payload(finding, component_field_id, line_field_id, severity_field_id)
                response = self.session.post(
                    f"{self.api_base}/rest/api/3/issue",
                    json=payload,
                    auth=self.auth,
                    headers=self.headers,
                )

        self._raise_for_status(response, "create issue")
        issue_key = response.json()["key"]

        # Both best-effort - must never fail ticket creation itself.
        try:
            self._sprints.add_issue(issue_key)
        except Exception as e:
            activity.logger.warning(f"Sprint assignment failed for {issue_key}, leaving it in the backlog: {e}")

        try:
            self._create_remote_link(issue_key, finding.deep_link)
        except Exception as e:
            activity.logger.warning(f"Remote link creation failed for {issue_key}, deep link not attached: {e}")

        return issue_key

    def attach_screenshot(self, issue_key: str, image_path: Path) -> None:
        """Skips the upload if a file with the same name is already attached (retry safety)."""
        get_response = self.session.get(
            f"{self.api_base}/rest/api/3/issue/{issue_key}",
            params={"fields": "attachment"},
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(get_response, "get attachments")

        existing = get_response.json().get("fields", {}).get("attachment", [])
        if any(attachment.get("filename") == image_path.name for attachment in existing):
            activity.logger.warning(
                f"Attachment {image_path.name} already exists on {issue_key}; skipping upload"
            )
            return

        with open(image_path, "rb") as f:
            response = self.session.post(
                f"{self.api_base}/rest/api/3/issue/{issue_key}/attachments",
                auth=self.auth,
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (image_path.name, f, "image/png")},
            )
        self._raise_for_status(response, "attach screenshot")

    def add_comment(self, ticket_key: str, body: str) -> None:
        payload: dict[str, Any] = {"body": doc(code_block(body))}
        response = self.session.post(
            f"{self.api_base}/rest/api/3/issue/{ticket_key}/comment",
            json=payload,
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "add comment")

    def get_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.api_base}/rest/api/3/issue/{issue_key}/transitions",
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "get transitions")
        return response.json().get("transitions", [])

    def transition_to_done(self, issue_key: str) -> bool:
        """Moves to whichever available transition leads to a done-category status. False if none is available."""
        for transition in self.get_transitions(issue_key):
            if transition.get("to", {}).get("statusCategory", {}).get("key") != "done":
                continue
            response = self.session.post(
                f"{self.api_base}/rest/api/3/issue/{issue_key}/transitions",
                json={"transition": {"id": transition["id"]}},
                auth=self.auth,
                headers=self.headers,
            )
            self._raise_for_status(response, "transition issue")
            return True
        return False

    def _update_ticket(self, issue_key: str, summary: str, description: dict) -> None:
        payload = {"fields": {"summary": summary, "description": description}}
        response = self.session.put(
            f"{self.api_base}/rest/api/3/issue/{issue_key}",
            json=payload,
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "update issue")

    def _build_rollup_summary(self, count: int) -> str:
        return f"SonarQube backlog: {count} additional finding(s) not yet ticketed"

    def _build_rollup_description(self, remaining: list[Finding]) -> dict:
        lines = [
            f"{_title_prefix(finding)}{finding.key} - {finding.finding_type} - "
            f"{finding.severity.value} - {finding.title}"
            for finding in remaining[:_ROLLUP_DESCRIPTION_MAX_LINES]
        ]
        if len(remaining) > _ROLLUP_DESCRIPTION_MAX_LINES:
            lines.append(f"... and {len(remaining) - _ROLLUP_DESCRIPTION_MAX_LINES} more")

        return doc(
            paragraph(
                "These findings were detected but not individually ticketed this run "
                "(per-run backlog cap reached) - they'll get their own ticket automatically "
                "in a future run as capacity frees up."
            ),
            bullet_list(lines),
        )

    def upsert_rollup_ticket(self, remaining: list[Finding]) -> str | None:
        """One shared ticket for findings that missed the per-run cap, updated in place rather than recreated."""
        existing = self._find_by_label(ROLLUP_LABEL)
        if not remaining and existing is None:
            return None

        summary = self._build_rollup_summary(len(remaining))
        description = self._build_rollup_description(remaining)

        if existing is not None:
            self._update_ticket(existing, summary=summary, description=description)
            return existing

        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary,
                "issuetype": {"name": "Task"},
                "labels": ["source-sonarqube", ROLLUP_LABEL],
                "description": description,
            }
        }
        response = self.session.post(
            f"{self.api_base}/rest/api/3/issue",
            json=payload,
            auth=self.auth,
            headers=self.headers,
        )
        self._raise_for_status(response, "create rollup issue")
        return response.json()["key"]
