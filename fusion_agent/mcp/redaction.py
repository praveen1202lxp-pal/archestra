"""Ephemeral In-Memory Secret Redaction for MCP Tool Operations.

CRITICAL INVARIANT:
In addition to regex-based pattern filtering (via SecretFilter), actual secret values
resolved dynamically for an MCP process are kept in an ephemeral in-memory set.
Before:
  - logging
  - SQLite persistence
  - ToolResult delivery
  - CodeContext prompt insertion
all strings, nested dictionaries, and lists are recursively scrubbed against known secrets.
The secret redaction set itself is ephemeral and NEVER persisted to disk or database.
"""

from typing import Any, Optional, Set

from fusion_agent.repository.ignore import SecretFilter


def recursive_redact_secrets(
    data: Any,
    secrets: Optional[Set[str]] = None,
    secret_filter: Optional[SecretFilter] = None,
) -> Any:
    """Recursively redact exact occurrences of known secrets and pattern-matched credentials."""
    if secrets is None:
        secrets = set()

    # Filter out empty or trivially short secrets (< 3 chars) to avoid over-redaction
    valid_secrets = sorted([s for s in secrets if s and len(s) >= 3], key=len, reverse=True)

    if isinstance(data, str):
        result = data
        for s in valid_secrets:
            result = result.replace(s, "[REDACTED_SECRET]")
        if secret_filter:
            result = secret_filter.filter_text(result)
        return result

    elif isinstance(data, dict):
        return {
            recursive_redact_secrets(k, secrets, secret_filter): recursive_redact_secrets(v, secrets, secret_filter)
            for k, v in data.items()
        }

    elif isinstance(data, list):
        return [recursive_redact_secrets(x, secrets, secret_filter) for x in data]

    elif isinstance(data, tuple):
        return tuple(recursive_redact_secrets(x, secrets, secret_filter) for x in data)

    elif isinstance(data, set):
        return {recursive_redact_secrets(x, secrets, secret_filter) for x in data}

    return data
