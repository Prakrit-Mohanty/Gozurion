# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Paths that gozu never makes a ticket for, whichever scanner reported them."""

IGNORED_DIRECTORIES = frozenset({".github"})


def is_ignored_path(component: str) -> bool:
    """True when `component` sits inside an ignored directory, at any depth.

    Accepts a plain path (`.github/workflows/ci.yml`) or SonarQube's
    `project_key:path` form - only what follows the first colon is the path.
    """
    path = component.split(":", 1)[-1].replace("\\", "/")
    return any(part in IGNORED_DIRECTORIES for part in path.split("/")[:-1])
