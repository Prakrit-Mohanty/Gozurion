"""Read environment variables as an env file means them.

A platform can copy the lines of an env file without change. A value can keep its quotes or its spaces.
"""

from __future__ import annotations

from collections.abc import Mapping


def env_text(environ: Mapping[str, str], name: str, default: str = "") -> str:
    """Return the value of a variable. Remove the spaces around it and one pair of matching quotes."""
    value = (environ.get(name) or default).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value
