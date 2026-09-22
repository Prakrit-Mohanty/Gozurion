# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: GitHub REST API - repo default branch lookup

"""
Stamps git/GitHub metadata onto the report (see docs/REPORT_CONTRACT.md):
commit_sha, branch, repo_full_name, default_branch. Prefers GitHub Actions'
own env vars (set automatically on every run) over shelling out to git,
which also works outside Actions.
"""

import logging
import os
import re
import subprocess

import requests

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

# git@github.com:owner/repo.git or https://github.com/owner/repo.git
_REMOTE_URL_RE = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?$")


def _run_git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def commit_sha() -> str | None:
    return os.environ.get("GITHUB_SHA") or _run_git("rev-parse", "HEAD")


def branch() -> str | None:
    return (
        os.environ.get("GITHUB_HEAD_REF")  # PR runs: the actual feature branch, not the merge ref
        or os.environ.get("GITHUB_REF_NAME")
        or _run_git("rev-parse", "--abbrev-ref", "HEAD")
    )


def repo_full_name() -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = _run_git("remote", "get-url", "origin")
    if not url:
        return None
    match = _REMOTE_URL_RE.search(url)
    return match.group(1) if match else None


def default_branch(repo: str | None) -> str | None:
    """Queries the GitHub API - a local branch name alone can't tell you which one is trunk."""
    if not repo:
        return None
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(f"{_GITHUB_API}/repos/{repo}", headers=headers, timeout=10)
    except requests.RequestException as e:
        logger.warning(f"Couldn't reach GitHub to look up {repo}'s default branch: {e}")
        return None
    if response.status_code != 200:
        logger.warning(f"GitHub repo lookup for {repo} failed with status {response.status_code} - default_branch unset")
        return None
    return response.json().get("default_branch")
