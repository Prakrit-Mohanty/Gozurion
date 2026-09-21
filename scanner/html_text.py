# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""Strips SonarQube's HTML-marked-up rule descriptions down to plain text."""

from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "br", "div"}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self, preserve_block_breaks: bool) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._preserve_block_breaks = preserve_block_breaks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._preserve_block_breaks and tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(marked_up: str, preserve_block_breaks: bool = False) -> str:
    extractor = _HTMLTextExtractor(preserve_block_breaks)
    extractor.feed(marked_up)
    text = extractor.text()
    if not preserve_block_breaks:
        return text

    collapsed: list[str] = []
    for line in (line.strip() for line in text.splitlines()):
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()
