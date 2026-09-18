# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Vendored from SonarToJira/SonarQube-to-Jira (package "gozu") - see
# core/models.py's Finding/Severity. Copied rather than depended-on because
# gozu's own dependencies (anthropic>=1.4.0, temporalio, psycopg, ...) hard-
# conflict with aetherion-sdk's pinned transitive deps (langchain-anthropic
# requires anthropic<1). core/ and ticket/ don't import anthropic/psycopg
# themselves, so copying just these is the smallest thing that works.
# Keep in sync with gozu manually if either side changes.

