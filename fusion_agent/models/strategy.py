"""Deliberation and execution strategy types."""

from enum import Enum


class StrategyType(str, Enum):
    """Collaboration strategies supported by Fusion Agent."""
    DIRECT = "DIRECT"
    EXECUTE_AND_REVIEW = "EXECUTE_AND_REVIEW"
    PROPOSE_CRITIQUE_REFINE = "PROPOSE_CRITIQUE_REFINE"
    INDEPENDENT_INVESTIGATION = "INDEPENDENT_INVESTIGATION"
    DUAL_PROPOSAL = "DUAL_PROPOSAL"
    DEBUGGING_LOOP = "DEBUGGING_LOOP"
