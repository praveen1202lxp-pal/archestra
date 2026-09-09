"""Shared Structured Output Normalization for Fusion Agent CLI Providers."""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fusion_agent.models.deliberation import ReviewStatus


@dataclass
class StructuredAgentOutput:
    """Canonical domain representation of an agent's structured response."""
    summary: str
    proposal: str
    findings: List[str] = field(default_factory=list)
    implementation_plan: List[str] = field(default_factory=list)
    confidence: float = 1.0
    recommended_next_action: str = ""

    def to_formatted_text(self) -> str:
        """Render the structured output as clean, human-readable markdown."""
        parts = []
        if self.summary:
            parts.append(f"**Summary**: {self.summary}\n")
        if self.findings:
            parts.append("**Findings**:\n" + "\n".join(f"- {f}" for f in self.findings) + "\n")
        if self.proposal:
            parts.append(f"**Proposal / Solution**:\n{self.proposal}\n")
        if self.implementation_plan:
            parts.append("**Implementation Plan**:\n" + "\n".join(f"{i+1}. {step}" for i, step in enumerate(self.implementation_plan)) + "\n")
        if self.recommended_next_action:
            parts.append(f"**Next Action**: {self.recommended_next_action}")
        return "\n".join(parts).strip()


class StructuredOutputNormalizer:
    """Parser and normalizer converting vendor CLI outputs into canonical domain models."""

    @classmethod
    def normalize(
        cls,
        raw_input: Any,
        fallback_summary: str = "Analysis completed.",
    ) -> StructuredAgentOutput:
        """Extract structured domain output from raw JSON dict, JSON string, markdown, or plain text."""
        # 1. If raw_input is already a dictionary (e.g., from parsed native JSON)
        if isinstance(raw_input, dict):
            # Check if an inner text field contains JSON
            inner_text = (
                raw_input.get("response")
                or raw_input.get("content")
                or raw_input.get("text")
                or raw_input.get("proposal")
            )
            if inner_text and isinstance(inner_text, str):
                try:
                    inner_json = json.loads(inner_text)
                    if isinstance(inner_json, dict):
                        return cls._from_dict(inner_json, fallback_summary)
                except Exception:
                    pass

                # If inner text has markdown code block
                mb = cls._extract_markdown_json(inner_text)
                if mb:
                    return cls._from_dict(mb, fallback_summary)

                return cls._from_text(inner_text, fallback_summary)

            return cls._from_dict(raw_input, fallback_summary)

        text = str(raw_input).strip() if raw_input else ""
        if not text:
            return StructuredAgentOutput(
                summary=fallback_summary,
                proposal=fallback_summary,
                confidence=1.0,
            )

        # 2. Try parsing entire text as top-level JSON
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return cls._from_dict(data, fallback_summary)
        except Exception:
            pass

        # 3. Check for markdown fenced code blocks: ```json { ... } ```
        mb = cls._extract_markdown_json(text)
        if mb:
            return cls._from_dict(mb, fallback_summary)

        # 4. Defensive fallback: extract lines and bullet points
        return cls._from_text(text, fallback_summary)

    @classmethod
    def _extract_markdown_json(cls, text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON object from markdown fenced code block."""
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return None

    @classmethod
    def _from_dict(cls, data: Dict[str, Any], fallback_summary: str) -> StructuredAgentOutput:
        """Create StructuredAgentOutput from a parsed JSON dictionary."""
        summary = (
            data.get("summary")
            or data.get("title")
            or fallback_summary
        )
        proposal = (
            data.get("proposal")
            or data.get("response")
            or data.get("content")
            or data.get("text")
            or summary
        )
        findings = data.get("findings", [])
        if isinstance(findings, str):
            findings = [findings]
        elif not isinstance(findings, list):
            findings = []

        implementation_plan = data.get("implementation_plan", data.get("plan", []))
        if isinstance(implementation_plan, str):
            implementation_plan = [implementation_plan]
        elif not isinstance(implementation_plan, list):
            implementation_plan = []

        try:
            confidence = float(data.get("confidence", 1.0))
        except (ValueError, TypeError):
            confidence = 1.0

        recommended_next_action = str(
            data.get("recommended_next_action")
            or data.get("next_action")
            or ""
        )

        return StructuredAgentOutput(
            summary=str(summary),
            proposal=str(proposal),
            findings=[str(f) for f in findings],
            implementation_plan=[str(s) for s in implementation_plan],
            confidence=confidence,
            recommended_next_action=recommended_next_action,
        )

    @classmethod
    def _from_text(cls, text: str, fallback_summary: str) -> StructuredAgentOutput:
        """Parse structured fields defensively from raw prose."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        summary = lines[0] if lines else fallback_summary
        findings = []
        for line in lines[1:]:
            if line.startswith(("-", "*", "•")) and len(line) > 2:
                findings.append(line.lstrip("-*• ").strip())
            if len(findings) >= 5:
                break

        return StructuredAgentOutput(
            summary=summary,
            proposal=text,
            findings=findings,
            confidence=0.95,
            recommended_next_action="Review and apply proposal",
        )

    @classmethod
    def parse_review_status(cls, text: str) -> ReviewStatus:
        """Determine ReviewStatus from review comments."""
        upper = text.upper()
        if "REJECT" in upper:
            return ReviewStatus.REJECTED
        if any(w in upper for w in ("NEEDS_REVISION", "REVISION", "FIX", "CRITIQUE")):
            return ReviewStatus.NEEDS_REVISION
        if "APPROV" in upper:
            return ReviewStatus.APPROVED
        return ReviewStatus.APPROVED
