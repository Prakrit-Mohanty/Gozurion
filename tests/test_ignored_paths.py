# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

import pytest

from core.ignored_paths import is_ignored_path
from core.models import Finding, Severity
from scanner import export


@pytest.mark.parametrize(
    "component, ignored",
    [
        (".github/workflows/ci.yml", True),
        ("proj:.github/workflows/ci.yml", True),
        ("services/api/.github/ci.yml", True),
        ("src/app.py", False),
        ("github/workflows/ci.yml", False),
        (".githubx/ci.yml", False),
    ],
)
def test_ignored_path_rule(component, ignored):
    assert is_ignored_path(component) is ignored


def make_finding(component):
    return Finding(
        key=f"semgrep:{component}",
        title="eval detected",
        severity=Severity.MEDIUM,
        component=component,
        line=5,
        message="Avoid eval.",
        finding_type="vulnerability",
        deep_link="https://example.invalid/finding/1",
        source_tool="semgrep",
    )


def test_the_report_holds_no_finding_from_the_github_dir(monkeypatch):
    meta = export.GitMetadata(branch="main", commit_sha="abc1234567", repo_full_name="acme/app", default_branch="main")
    monkeypatch.setenv("ENABLED_SCANNERS", "semgrep")
    monkeypatch.setitem(
        export._SCANNERS,
        "semgrep",
        lambda m: [make_finding(".github/workflows/ci.yml"), make_finding("backend/app.py")],
    )

    reports = export.build_reports(meta)

    assert [f["component"] for f in reports["combined"]] == ["backend/app.py"]
