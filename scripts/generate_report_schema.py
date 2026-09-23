# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
Generates docs/report-schema.json from core.models.Finding - the exact
contract for the JSON report `fetch_report_from_s3` downloads and
`create_jira_tickets` validates. Single source of truth is Finding
itself; run this after changing it so the schema can't drift.

    python3 scripts/generate_report_schema.py
"""

import json
from pathlib import Path

from core.models import Finding

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "report-schema.json"


def build_schema() -> dict:
    finding_schema = Finding.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "SonarToJira findings report",
        "description": "A report object is a JSON array of these Finding objects, uploaded to S3.",
        "type": "array",
        "items": finding_schema,
    }


if __name__ == "__main__":
    OUTPUT_PATH.write_text(json.dumps(build_schema(), indent=2) + "\n")
    print(f"Wrote {OUTPUT_PATH}")
