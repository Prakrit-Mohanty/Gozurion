# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
Builds the PNG attached to every ticket (see JiraClient.attach_screenshot).

Line-based findings (semgrep, sonarqube, trivy secrets) get a
syntax-highlighted snippet of the actual source around finding.line,
fetched from GitHub at the commit the finding was scanned at. Line-less
findings (trivy dependency vulnerabilities - component is a package name,
not a source location) get a plain info card instead, since there's no
source line to show.
"""

import io

from PIL import Image, ImageDraw, ImageFont
from pygments import highlight
from pygments.formatters import ImageFormatter
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.util import ClassNotFound

import requests

from core.models import Finding

_SNIPPET_CONTEXT_LINES = 7  # lines of source shown above/below finding.line
_GITHUB_RAW_TIMEOUT_SECONDS = 10
_CARD_FONT_SIZE = 16
_CARD_LINE_HEIGHT = 24
_CARD_PADDING = 20
_CARD_WIDTH = 900


def _relative_path(component: str) -> str:
    """SonarQube's `component` is "{project_key}:{path}"; semgrep/trivy already use a plain relative path."""
    return component.split(":", 1)[-1] if ":" in component else component


def _fetch_source(finding: Finding, github_token: str | None) -> str | None:
    if not (finding.repo_full_name and finding.commit_sha):
        return None
    path = _relative_path(finding.component)
    url = f"https://raw.githubusercontent.com/{finding.repo_full_name}/{finding.commit_sha}/{path}"
    headers = {"Authorization": f"token {github_token}"} if github_token else {}
    try:
        response = requests.get(url, headers=headers, timeout=_GITHUB_RAW_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None
    return response.text if response.status_code == 200 else None


def _snippet_around(source: str, line: int) -> tuple[str, int]:
    """Returns (snippet_text, 1-indexed line number the snippet starts at)."""
    lines = source.splitlines()
    start = max(1, line - _SNIPPET_CONTEXT_LINES)
    end = min(len(lines), line + _SNIPPET_CONTEXT_LINES)
    return "\n".join(lines[start - 1 : end]), start


def _render_code_snippet(finding: Finding, snippet: str, start_line: int) -> bytes:
    path = _relative_path(finding.component)
    try:
        lexer = get_lexer_for_filename(path, stripnl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False)

    formatter = ImageFormatter(
        line_numbers=True,
        line_number_start=start_line,
        hl_lines=[finding.line - start_line + 1],
        font_size=_CARD_FONT_SIZE,
        style="monokai",
    )
    return highlight(snippet, lexer, formatter)


def _render_info_card(finding: Finding) -> bytes:
    """No source line available (e.g. a Trivy dependency vulnerability) - a plain text card instead."""
    lines = [
        finding.title,
        f"Component: {finding.component}",
        f"Severity: {finding.severity.value}",
        f"Rule: {finding.rule_key or 'n/a'}",
        "",
        finding.message,
    ]
    if finding.how_to_fix:
        lines += ["", f"Fix: {finding.how_to_fix}"]

    font = ImageFont.load_default(size=_CARD_FONT_SIZE)
    height = _CARD_PADDING * 2 + _CARD_LINE_HEIGHT * len(lines)
    image = Image.new("RGB", (_CARD_WIDTH, height), color="#1e1e1e")
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(lines):
        draw.text((_CARD_PADDING, _CARD_PADDING + i * _CARD_LINE_HEIGHT), text, fill="#d4d4d4", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_screenshot(finding: Finding, github_token: str | None = None) -> bytes:
    """PNG bytes for the given finding - a code snippet if finding.line and its
    source are both available, otherwise a plain info card."""
    source = _fetch_source(finding, github_token) if finding.line else None
    if source:
        snippet, start_line = _snippet_around(source, finding.line)
        return _render_code_snippet(finding, snippet, start_line)
    return _render_info_card(finding)
