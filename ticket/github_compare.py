# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: GitHub REST API - commit ancestry check

"""
Tells a genuine regression (fix reverted on the default branch) from a
stale branch still carrying an old bug - see ticket/claims.py's
closed_at_commit_sha and src/tools/tools.py.
"""

import os

import requests

_GITHUB_API = "https://api.github.com"


def is_ancestor(repo_full_name: str, base_sha: str, head_sha: str) -> bool:
    """True if `base_sha` is an ancestor of `head_sha` in `repo_full_name`. False (not raised) if this can't be confirmed - callers must treat that conservatively."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        f"{_GITHUB_API}/repos/{repo_full_name}/compare/{base_sha}...{head_sha}",
        headers=headers,
    )
    if response.status_code != 200:
        return False

    return response.json().get("status") in ("ahead", "identical")
