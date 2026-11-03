# Copyright (c) 2026 Calfus Inc.
# Author: Prakrit Mohanty

"""ASD-STE100 rewrite of ticket text.

The agent sends batches of new findings to the hosted llm_chat tool with the
prompt from build_prompt(), and merges the reply back with apply_reply(). The
reply sets Finding.llm_explanation (the Description paragraph) and
Finding.recommended_action (the Recommended action paragraph).

Pure functions only (json/re, no I/O): the agent's workflow code calls them
directly, between activities.
"""

from __future__ import annotations

import json
import re

BATCH_SIZE = 15

RULES = """Write in ASD-STE100 Simplified Technical English. Obey these rules:
- Use simple, common words. Use one word for one meaning. Use the same term each time.
- Use the active voice and the present tense.
- A descriptive sentence has at most 25 words. An instruction has at most 20 words.
- Write an instruction in the imperative ("Pin the reference."). Write one instruction in each sentence.
- Use articles ("the", "a") where they can go.
- Do not use contractions, slang, phrasal verbs or words that end in "-ing" as nouns.
- Do not use "should", "could" or "might" in an instruction. Tell the reader what to do."""

PROMPT = """You write the text of security tickets in Jira. Each finding below comes from a code scanner.

For each finding, write two texts:
1. "description": 2 to 4 sentences. Say what the problem is and why it is a risk. Do not repeat the repository, file, line, rule ID, severity or branch: the ticket shows them in a separate list.
2. "recommended_action": 1 to 3 instructions. Tell the developer what to change to fix this finding. Use the file and the line only if they make the instruction clearer.

Use only the facts in the finding. Do not invent versions, function names or values.

{rules}

Reply with one JSON object and nothing else. Use each finding "key" as a property name:
{{"<key>": {{"description": "...", "recommended_action": "..."}}}}

Findings:
{findings}"""

_FIELDS = ("key", "title", "rule_key", "finding_type", "severity", "source_tool", "component", "line")


def _brief(finding: dict) -> dict:
    """The parts of a finding the LLM needs, with the long texts cut short."""
    brief = {name: finding.get(name) for name in _FIELDS if finding.get(name) is not None}
    brief["message"] = (finding.get("message") or "")[:1500]
    if finding.get("how_to_fix"):
        brief["how_to_fix"] = finding["how_to_fix"][:800]
    return brief


def batches(findings: list[dict], size: int = BATCH_SIZE) -> list[list[dict]]:
    return [findings[i : i + size] for i in range(0, len(findings), size)]


def build_prompt(findings: list[dict]) -> str:
    return PROMPT.format(rules=RULES, findings=json.dumps([_brief(f) for f in findings], indent=1))


def _parse(reply: str | None) -> dict:
    """The JSON object in the reply, or {} if there is none. Models sometimes wrap it in a code fence."""
    if not reply:
        return {}
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(reply[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(text) -> str | None:
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000] or None


def apply_reply(findings: list[dict], reply: str | None) -> list[dict]:
    """Copies of `findings` with llm_explanation/recommended_action set from the reply. A finding
    the reply leaves out, or gives no usable text for, keeps the scanner's own text."""
    texts = _parse(reply)
    merged = []
    for finding in findings:
        finding = dict(finding)
        entry = texts.get(finding.get("key"))
        if isinstance(entry, dict):
            if description := _clean(entry.get("description")):
                finding["llm_explanation"] = description
            if action := _clean(entry.get("recommended_action")):
                finding["recommended_action"] = action
        merged.append(finding)
    return merged
