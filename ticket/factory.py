# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
# Editor: Prakrit Mohanty

"""Builds a TicketClient from explicit credentials, or from the environment."""

import os

from ticket.base import TicketClient
from ticket.jira_client import JiraClient


def build_ticket_client(ticket_backend: str, credentials: dict[str, str]) -> TicketClient:
    """Pure constructor - no env reads."""
    if ticket_backend != "jira":
        raise ValueError(f"Unrecognized ticket_backend '{ticket_backend}'. Expected 'jira'.")

    return JiraClient(
        base_url=credentials["jira_url"],
        email=credentials["jira_email"],
        api_token=credentials["jira_api_token"],
        project_key=credentials["jira_project_key"],
    )


def get_ticket_client() -> TicketClient:
    """Reads TICKET_BACKEND/JIRA_* from the environment (legacy webhook-receiver path)."""
    backend = os.environ.get("TICKET_BACKEND", "jira")
    credentials = {
        "jira_url": os.environ["JIRA_URL"],
        "jira_email": os.environ["JIRA_EMAIL"],
        "jira_api_token": os.environ["JIRA_API_TOKEN"],
        "jira_project_key": os.environ["JIRA_PROJECT_KEY"],
    }
    return build_ticket_client(backend, credentials)
