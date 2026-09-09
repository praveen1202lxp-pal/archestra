"""Parses agent code modifications and applies them strictly through ExecutionBroker."""

import json
import re
from pathlib import Path
from typing import List, Tuple

from fusion_agent.workspace.broker import ExecutionBroker


class WorkspaceEditor:
    """Extracts file additions and modifications from agent responses and applies them via broker."""

    # Patterns for detecting file blocks in agent outputs
    FILE_BLOCK_PATTERNS = [
        # Pattern 1: ### File: path/to/file.ext followed by code fence
        re.compile(
            r"[ \t]*###\s+File:?\s*[`'\"]?([a-zA-Z0-9_\-./\\]+)[`'\"]?\s*\n+[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
        # Pattern 2: Code fence with header line containing filepath: path/to/file.ext
        re.compile(
            r"[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n[ \t]*(?:#|//|<!--|;)\s*(?:filepath:|file:)\s*([a-zA-Z0-9_\-./\\]+)\s*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
        # Pattern 3: **File**: `path/to/file.ext` followed by code fence
        re.compile(
            r"[ \t]*\*\*File\*\*:?\s*[`'\"]([a-zA-Z0-9_\-./\\]+)[`'\"]\s*\n+[ \t]*```[a-zA-Z0-9_\-#+]*\s*\n(.*?)\n[ \t]*```",
            re.DOTALL | re.IGNORECASE,
        ),
    ]

    @classmethod
    def extract_file_edits(cls, response_text: str) -> List[Tuple[str, str]]:
        """Extract a list of (relative_path, content) pairs from agent response text."""
        import textwrap

        clean_text = textwrap.dedent(response_text)
        edits: List[Tuple[str, str]] = []
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
                            if clean_p not in seen_paths:
                                seen_paths.add(clean_p)
                                edits.append((clean_p, item["content"]))
            except Exception:
                pass

        if edits:
            return edits

        # 2. Check regex block patterns
        for pattern in cls.FILE_BLOCK_PATTERNS:
            for match in pattern.finditer(clean_text):
                rel_path = match.group(1).strip().replace("\\", "/")
                content = textwrap.dedent(match.group(2)).strip()
                if rel_path not in seen_paths:
                    seen_paths.add(rel_path)
                    edits.append((rel_path, content))

        return edits

    @classmethod
    def apply_edits(cls, response_text: str, broker: ExecutionBroker) -> List[str]:
        """Parse and write all extracted file modifications using the execution broker."""
        extracted = cls.extract_file_edits(response_text)
        modified_paths = []

        for rel_path, content in extracted:
            broker.write_file(rel_path, content)
            modified_paths.append(rel_path)

        return modified_paths
