# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

import json
from pathlib import Path

import pytest

from core.errors import MissingRepositoryError
from core.models import Finding, Severity
from scanner import export, git_context
from scanner.trivy_scanner import TrivyClient
from ticket import jira_client

GOLDEN = json.loads((Path(__file__).parent / "contract" / "golden_tickets.json").read_text())
CASES = {case["name"]: case for case in GOLDEN["cases"]}


def make_finding(spec: dict, **changes) -> Finding:
    values = dict(
        key=f"{spec['source']}:1",
        title=spec["title"],
        severity=Severity[spec["severity"]],
        component=spec["component"],
        line=spec["line"],
        message="The scanner message.",
        finding_type=spec["type"],
        deep_link="https://example.invalid/finding/1",
        source_tool=spec["source"],
        branch=spec["branch"],
        commit_sha=spec["commit"],
        repo_full_name=spec["repo"],
        rule_key=spec["rule"],
        package=spec.get("package"),
        installed_version=spec.get("installed_version"),
        fixed_version=spec.get("fixed_version"),
    )
    values.update(changes)
    return Finding(**values)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(jira_client, "_resolve_api_base", lambda url: "https://api.invalid")
    return jira_client.JiraClient(
        "https://x.atlassian.net", "bot@example.invalid", "token-value", "VT"
    )


def details_of(adf: dict) -> list[str]:
    """The lines of the list under the heading Details."""
    content = adf["content"]
    for index, node in enumerate(content):
        if node["type"] == "heading" and node["content"][0]["text"] == "Details":
            items = content[index + 1]["content"]
            return [
                "".join(part["text"] for part in item["content"][0]["content"]) for item in items
            ]
    raise AssertionError("The ticket has no Details list.")


@pytest.mark.parametrize("name", list(CASES))
def test_the_title_follows_the_contract(client, name):
    spec = CASES[name]["finding"]

    title = client._build_summary(make_finding(spec))

    assert title == CASES[name]["expected"]["title"]
    assert len(title) <= 120


@pytest.mark.parametrize("name", list(CASES))
def test_the_labels_follow_the_contract(client, name):
    spec = CASES[name]["finding"]

    assert client._build_labels(make_finding(spec)) == CASES[name]["expected"]["labels"]


@pytest.mark.parametrize("name", list(CASES))
def test_the_details_list_follows_the_contract(client, name):
    spec = CASES[name]["finding"]

    description = client._build_description(make_finding(spec))

    assert details_of(description) == CASES[name]["expected"]["details"]


@pytest.mark.parametrize("name", list(CASES))
def test_the_priority_follows_the_severity(client, name):
    spec = CASES[name]["finding"]

    assert client._map_priority(make_finding(spec).severity) == CASES[name]["expected"]["priority"]


def test_the_same_finding_on_two_branches_gets_the_same_issue_label(client):
    spec = CASES["a finding with a line"]["finding"]

    first = client._build_labels(make_finding(spec, branch="main"))
    second = client._build_labels(make_finding(spec, branch="feature/other"))

    assert first[0] == second[0]
    assert first[0].startswith("issue-")


@pytest.mark.parametrize("repo", [None, "", "   "])
def test_a_ticket_is_never_created_without_a_repository(client, repo):
    spec = CASES["a finding with a line"]["finding"]

    def no_call(*args, **kwargs):
        raise AssertionError("No request may go out for a finding without a repository.")

    client.session.post = no_call

    with pytest.raises(MissingRepositoryError):
        client.create_ticket(make_finding(spec, repo_full_name=repo))


def test_the_exporter_stops_when_it_cannot_tell_the_repository(monkeypatch):
    monkeypatch.setattr(git_context, "branch", lambda: "main")
    monkeypatch.setattr(git_context, "repo_full_name", lambda: None)
    monkeypatch.setattr(git_context, "commit_sha", lambda: "abc1234567")
    monkeypatch.setattr(git_context, "default_branch", lambda repo: "main")

    with pytest.raises(MissingRepositoryError):
        export.gather_git_metadata()


PACKAGE = CASES["a package finding with the package lines"]["finding"]


def test_a_trivy_finding_with_no_fixed_version_has_no_fixed_version_line(client):
    finding = make_finding(PACKAGE, fixed_version=None)

    details = details_of(client._build_description(finding))

    assert "Package: lodash" in details
    assert "Installed version: 4.17.15" in details
    assert not any(line.startswith("Fixed version") for line in details)
    assert not any(line.startswith("Line") for line in details)


def test_a_finding_with_no_package_has_no_package_lines(client):
    spec = CASES["a finding with a line"]["finding"]

    details = details_of(client._build_description(make_finding(spec)))

    assert not any(line.startswith(("Package", "Installed", "Fixed")) for line in details)


def test_the_ticket_never_carries_the_breaking_change_label(client):
    assert "breaking-change" not in client._build_labels(make_finding(PACKAGE))


def trivy_vulnerability(**changes) -> dict:
    vuln = {
        "VulnerabilityID": "CVE-2021-23337",
        "PkgName": "lodash",
        "InstalledVersion": "4.17.15",
        "FixedVersion": "1.2.5, 2.0.1",
        "Title": "lodash: command injection",
        "Severity": "HIGH",
    }
    return vuln | changes


def test_trivy_keeps_the_package_and_both_versions_as_trivy_gives_them():
    finding = TrivyClient()._from_vuln(trivy_vulnerability(), "package.json")

    assert (finding.package, finding.installed_version, finding.fixed_version) == (
        "lodash",
        "4.17.15",
        "1.2.5, 2.0.1",
    )
    assert finding.line is None


def test_trivy_gives_no_fixed_version_when_the_vulnerability_has_none():
    vuln = trivy_vulnerability()
    del vuln["FixedVersion"]

    finding = TrivyClient()._from_vuln(vuln, "package.json")

    assert finding.fixed_version is None
    assert finding.package == "lodash"


def test_trivy_secret_and_misconfig_findings_have_no_package():
    client = TrivyClient()

    secret = client._from_secret({"RuleID": "aws", "StartLine": 3}, "a.env")
    misconfig = client._from_misconfig({"ID": "DS001", "Title": "t"}, "Dockerfile")

    assert [secret.package, misconfig.package] == [None, None]
