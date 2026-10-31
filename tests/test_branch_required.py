# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

import json

import pytest

from core.errors import MissingBranchError
from core.models import Finding, Severity
from scanner import export, git_context
from ticket import jira_client


def make_finding(branch):
    return Finding(
        key="semgrep:1",
        title="eval detected",
        severity=Severity.MEDIUM,
        component="backend/app.py",
        line=5,
        message="Avoid eval.",
        finding_type="vulnerability",
        deep_link="https://example.invalid/finding/1",
        source_tool="semgrep",
        branch=branch,
        commit_sha="abc1234567",
        repo_full_name="acme/app",
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(jira_client, "_resolve_api_base", lambda url: "https://api.invalid")
    return jira_client.JiraClient("https://x.atlassian.net", "bot@example.invalid", "token-value", "VT")


@pytest.mark.parametrize("branch", [None, "", "   "])
def test_a_ticket_is_never_created_without_a_branch(client, branch):
    def no_call(*args, **kwargs):
        raise AssertionError("No request may go out for a finding without a branch.")

    client.session.post = no_call

    with pytest.raises(MissingBranchError):
        client.create_ticket(make_finding(branch))


def test_the_ticket_names_the_branch_in_the_details_and_in_a_label(client):
    payload = client._build_create_payload(make_finding("feature/login"), None, None)

    text = json.dumps(payload["fields"]["description"])
    assert "Branch: feature/login" in text
    assert "unknown" not in text.lower()
    assert "branch-feature/login" in payload["fields"]["labels"]


def test_the_exporter_stops_when_it_cannot_tell_the_branch(monkeypatch):
    monkeypatch.setattr(git_context, "branch", lambda: None)
    monkeypatch.setattr(git_context, "repo_full_name", lambda: "acme/app")
    monkeypatch.setattr(git_context, "commit_sha", lambda: "abc1234567")
    monkeypatch.setattr(git_context, "default_branch", lambda repo: "main")

    with pytest.raises(MissingBranchError):
        export.gather_git_metadata()


def test_the_report_key_never_holds_a_made_up_branch():
    with pytest.raises(MissingBranchError):
        export.latest_prefix("acme/app", None)


def test_the_upload_stops_before_it_writes_when_the_branch_is_missing(monkeypatch):
    meta = export.GitMetadata(branch=None, commit_sha="abc1234567", repo_full_name="acme/app", default_branch="main")

    def no_storage():
        raise AssertionError("Nothing may be written without a branch.")

    monkeypatch.setattr(export, "get_storage_client", no_storage)

    with pytest.raises(MissingBranchError):
        export.upload_reports({"combined": []}, meta)
