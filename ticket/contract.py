# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
The Gozu ticket contract: how a ticket looks, for people and for veil.
The same rules are in the Gozu CLI (sonar-to-jira) and in veil. See docs/TICKET_CONTRACT.md
and tests/contract/golden_tickets.json, which all three repos share.
"""


TITLE_MAX_LENGTH = 120


def ticket_title(repo: str, branch: str, path: str, line: int | None, title: str) -> str:
    """ "| repo | branch | file.py | 42 | Rule title", at most 120 characters.

    The repo is the name without the owner. The branch is whole. The file is the last part of the path.
    A finding with no line has no line part. If the title is too long, it is cut and ends with "...".
    The full repository and path are in the ticket details.
    """
    parts = [repo.strip().split("/")[-1], branch.strip(), path.strip().split("/")[-1]]
    if line is not None:
        parts.append(str(line))
    head = "| " + " | ".join(parts) + " | "
    text = " ".join(title.split())
    if len(head) + len(text) <= TITLE_MAX_LENGTH:
        return head + text
    room = max(TITLE_MAX_LENGTH - len(head) - 3, 10)
    return head + text[:room].rstrip() + "..."

