"""Structured logging and security sanitization for Fusion Agent."""

import logging
import os
import re
from pathlib import Path
from typing import Optional


class SensitiveFilter(logging.Filter):
    """Filter that masks API keys, secrets, and auth tokens from log output."""

    PATTERNS = [
        r"(AIza[0-9A-Za-z-_]{35})",                   # Google / Gemini API key
        r"(sk-[a-zA-Z0-9]{32,})",                     # OpenAI API key
        r"(Bearer\s+[A-Za-z0-9_\-\.]{15,})",           # Bearer token
        r"([a-zA-Z0-9_-]{20,}:[a-zA-Z0-9_-]{20,})",  # basic auth
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in self.PATTERNS:
                record.msg = re.sub(pattern, "[REDACTED_SECRET]", record.msg)
        return True


def setup_logger(name: str = "fusion_agent", log_file: Optional[Path] = None, level: str = "INFO") -> logging.Logger:
    """Configure and return a sanitized structured logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addFilter(SensitiveFilter())

    if not logger.handlers:
        # Console handler with clean format
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console)

        # Optional file handler
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_h = logging.FileHandler(str(log_file), encoding="utf-8")
            file_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            file_h.addFilter(SensitiveFilter())
            logger.addHandler(file_h)

    return logger
