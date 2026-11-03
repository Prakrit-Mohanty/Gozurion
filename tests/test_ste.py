# Copyright (c) 2026 Calfus Inc.
# Author: Prakrit Mohanty

import json

import pytest

from core.models import Finding, Severity
from ticket import jira_client, ste

FINDING = {
    "key": "semgrep:1",
    "title": "github-actions-mutable-action-tag",
    "severity": "MEDIUM",
    "component": ".github/workflows/export-scan-report.yml",
    "line": 26,
    "message": "An action sourced from a third-party repository is not pinned to a full length commit SHA.",
    "finding_type": "vulnerability",
    "deep_link": "https://example.invalid/finding/1",
    "source_tool": "semgrep",
    "branch": "veil-trail-source",
    "repo_full_name": "Wasi-cal/Odyssey-port6-RAG",
    "rule_key": "yaml.github-actions.security.github-actions-mutable-action-tag",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(jira_client, "_resolve_api_base", lambda url: "https://api.invalid")
    return jira_client.JiraClient("https://x.atlassian.net", "bot@example.invalid", "token-value", "VT")


def texts_of(adf: dict) -> list[str]:
    """The headings and paragraphs of a description, in order."""
    out = []
    for node in adf["content"]:
        if node["type"] in ("heading", "paragraph"):
            out.append("".join(part.get("text", "") for part in node.get("content", [])))
    return out


def test_the_prompt_names_the_rules_and_every_finding():
    prompt = ste.build_prompt([FINDING, {**FINDING, "key": "semgrep:2"}])

    assert "ASD-STE100" in prompt
    assert '"semgrep:1"' in prompt and '"semgrep:2"' in prompt


def test_a_reply_in_a_code_fence_is_applied():
    reply = "```json\n" + json.dumps(
        {"semgrep:1": {"description": "The step uses a tag.", "recommended_action": "Pin the reference."}}
    ) + "\n```"

    [merged] = ste.apply_reply([FINDING], reply)

    assert merged["llm_explanation"] == "The step uses a tag."
    assert merged["recommended_action"] == "Pin the reference."


@pytest.mark.parametrize("reply", [None, "", "not json", "[1, 2]", '{"other:9": {"description": "x"}}'])
def test_a_bad_reply_keeps_the_scanner_text(reply):
    [merged] = ste.apply_reply([FINDING], reply)

    assert "llm_explanation" not in merged
    assert "recommended_action" not in merged


def test_findings_are_sent_in_batches():
    findings = [{**FINDING, "key": f"semgrep:{i}"} for i in range(ste.BATCH_SIZE * 2 + 1)]

    assert [len(b) for b in ste.batches(findings)] == [ste.BATCH_SIZE, ste.BATCH_SIZE, 1]


def test_the_description_has_three_sections_in_order(client):
    finding = Finding.model_validate(
        {**FINDING, "llm_explanation": "The step uses a tag.", "recommended_action": "Pin the reference."}
    )

    texts = texts_of(client._build_description(finding))

    assert texts == ["Description", "The step uses a tag.", "Details", "Recommended action", "Pin the reference."]


def test_without_any_fix_text_the_action_is_to_review_the_location(client):
    finding = Finding.model_validate({**FINDING, "severity": Severity.MEDIUM})

    texts = texts_of(client._build_description(finding))

    assert texts[-2:] == [
        "Recommended action",
        "Review .github/workflows/export-scan-report.yml at line 26.",
    ]
