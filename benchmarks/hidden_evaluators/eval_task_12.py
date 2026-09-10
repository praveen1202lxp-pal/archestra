"""Hidden acceptance evaluator for TASK-12: Markdown parser trailing hashes."""

import pytest
from src.markdown_parser import parse_header


def test_hidden_trailing_hashes_stripped():
    assert parse_header("### Section Header ###") == "Section Header"
    assert parse_header("## Heading with Trailing ##") == "Heading with Trailing"
    assert parse_header("# Minimal #") == "Minimal"


def test_hidden_multiple_and_asymmetric_hashes():
    assert parse_header("### Deep Header #######") == "Deep Header"
    assert parse_header("## Header With Spaces Inside   ##   ") == "Header With Spaces Inside"


def test_hidden_non_header_lines_preserved():
    assert parse_header("Normal paragraph with # hashtag") == "Normal paragraph with # hashtag"
    assert parse_header("Issue #42 is closed") == "Issue #42 is closed"


def test_hidden_empty_header():
    assert parse_header("###") == ""
    assert parse_header("### ###") == ""
