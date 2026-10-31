# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
The Gozu ticket contract: how a ticket looks, for people and for veil.
The same rules are in the Gozu CLI (sonar-to-jira) and in veil. See docs/TICKET_CONTRACT.md
and tests/contract/golden_tickets.json, which all three repos share.
"""

import re

TITLE_MAX_LENGTH = 120


def ticket_title(repo: str, path: str, line: int | None, title: str) -> str:
    """ "[owner/repo] path/to/file.py:42 - Rule title", at most 120 characters.

    If it is longer, the path loses its leading folders and keeps its last two parts
    (".../folder/file.py"). If it is still longer, the rule title is cut and ends with "...".
    """
    text = " ".join(title.split())
    full = _join(repo, path, line, text)
    if len(full) <= TITLE_MAX_LENGTH:
        return full
    short = _short_path(path)
    full = _join(repo, short, line, text)
    if len(full) <= TITLE_MAX_LENGTH:
        return full
    head = _join(repo, short, line, "")
    room = max(TITLE_MAX_LENGTH - len(head) - 3, 10)
    return head + text[:room].rstrip() + "..."


def repo_label(repo: str) -> str:
    """ "repo-owner-name". A Jira label has no space, so every other mark becomes a hyphen."""
    return "repo-" + re.sub(r"[^a-z0-9._-]+", "-", repo.strip().lower())


def _join(repo: str, path: str, line: int | None, text: str) -> str:
    location = f"{path}:{line}" if line is not None else path
    return f"[{repo}] {location} - {text}"


def _short_path(path: str) -> str:
    parts = path.split("/")
    return path if len(parts) <= 2 else ".../" + "/".join(parts[-2:])
