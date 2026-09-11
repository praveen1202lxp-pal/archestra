"""Parses agent code modifications and applies them strictly through ExecutionBroker."""

import json
import re
import textwrap
from pathlib import Path
from typing import Any, List, Optional, Tuple

from fusion_agent.workspace.broker import ExecutionBroker


class WorkspaceEditor:
    """Extracts file additions and modifications from agent responses and applies them via broker."""

    # Patterns for detecting file blocks in agent outputs (with optional justification)
    FILE_BLOCK_PATTERNS = [
        # Pattern 1: ### File: path/to/file.ext (reason: ...) followed by code fence
        re.compile(
            r"[ \t]*###\s+File:?\s*[`'\"]?([a-zA-Z0-9_\-./\\]+)[`'\"]?(?:\s*\((?:reason|justification):\s*([^)\n]+)\))?[^\n]*\n+[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
        # Pattern 2: Code fence with header line containing filepath: path/to/file.ext
        re.compile(
            r"[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n[ \t]*(?:#|//|<!--|;)\s*(?:filepath:|file:)\s*([a-zA-Z0-9_\-./\\]+)(?:\s*\((?:reason|justification):\s*([^)\n]+)\))?[^\n]*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
        # Pattern 3: **File**: `path/to/file.ext` (reason: ...) followed by code fence
        re.compile(
            r"[ \t]*\*\*File\*\*:?\s*[`'\"]([a-zA-Z0-9_\-./\\]+)[`'\"](?:\s*\((?:reason|justification):\s*([^)\n]+)\))?[^\n]*\n+[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
    ]

    @classmethod
    def extract_file_edits_with_reasons(cls, response_text: str) -> List[Tuple[str, str, Optional[str]]]:
        """Extract a list of (relative_path, content, optional_reason) tuples from agent response text."""
        clean_text = textwrap.dedent(response_text)
        edits: List[Tuple[str, str, Optional[str]]] = []
        seen_paths = set()

        # 1. Check for JSON structure containing files array
        json_pattern = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
        for match in json_pattern.finditer(clean_text):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict) and "files" in data and isinstance(data["files"], list):
                    for item in data["files"]:
                        if isinstance(item, dict) and "path" in item and "content" in item:
                            clean_p = item["path"].strip().replace("\\", "/")
                            reason = item.get("reason") or item.get("justification")
                            if clean_p not in seen_paths:
                                seen_paths.add(clean_p)
                                edits.append((clean_p, item["content"], reason))
            except Exception:
                pass

        if edits:
            return edits

        # 2. Check regex block patterns
        for pattern in cls.FILE_BLOCK_PATTERNS:
            for match in pattern.finditer(clean_text):
                rel_path = match.group(1).strip().replace("\\", "/")
                reason = match.group(2).strip() if match.group(2) else None
                content = textwrap.dedent(match.group(3)).strip()
                if rel_path not in seen_paths:
                    seen_paths.add(rel_path)
                    edits.append((rel_path, content, reason))

        return edits

    @classmethod
    def extract_file_edits(cls, response_text: str) -> List[Tuple[str, str]]:
        """Extract a list of (relative_path, content) tuples from agent response text."""
        return [(rel, content) for rel, content, _ in cls.extract_file_edits_with_reasons(response_text)]

    @classmethod
    def apply_edits(
        cls,
        response_text: str,
        broker: ExecutionBroker,
        scope_contract: Optional[Any] = None,
        task_prompt: str = "",
        rejected_files: Optional[List[Tuple[str, str]]] = None,
    ) -> List[str]:
        """Parse and write all extracted file modifications using the execution broker.

        If scope_contract is provided, files are validated prior to writing.
        """
        extracted = cls.extract_file_edits_with_reasons(response_text)
        modified_paths = []

        for item in extracted:
            rel_path = item[0]
            content = item[1]
            explicit_reason = item[2] if len(item) > 2 else None

            if scope_contract is not None:
                exists = broker.file_exists(rel_path)
                is_allowed, reason = scope_contract.validate_file(
                    rel_path=rel_path,
                    is_new_file=(not exists),
                    task_prompt=task_prompt,
                    explicit_reason=explicit_reason,
                )
                if not is_allowed:
                    if rejected_files is not None:
                        rejected_files.append((rel_path, reason))
                    continue

            broker.write_file(rel_path, content)
            modified_paths.append(rel_path)

        return modified_paths
