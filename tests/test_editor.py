"""Unit tests for WorkspaceEditor file extraction and application."""

from pathlib import Path
from unittest.mock import MagicMock

from fusion_agent.workspace.editor import WorkspaceEditor


def test_extract_file_edits_header_format():
    """Extracts file blocks formatted with ### File: path/to/file."""
    text = """
    Here is the implementation:

    ### File: src/config.py
    ```python
    DEBUG = True
    PORT = 8080
    ```

    ### File: tests/test_config.py
    ```python
    def test_config():
        assert True
    ```
    """
    edits = WorkspaceEditor.extract_file_edits(text)
    assert len(edits) == 2
    assert edits[0][0] == "src/config.py"
    assert "DEBUG = True" in edits[0][1]
    assert edits[1][0] == "tests/test_config.py"
    assert "def test_config():" in edits[1][1]


def test_extract_file_edits_filepath_comment_format():
    """Extracts file blocks formatted with // filepath: path/to/file."""
    text = """
    ```javascript
    // filepath: static/app.js
    console.log("loaded");
    ```
    """
    edits = WorkspaceEditor.extract_file_edits(text)
    assert len(edits) == 1
    assert edits[0][0] == "static/app.js"
    assert 'console.log("loaded");' in edits[0][1]


def test_extract_file_edits_json_format():
    """Extracts file blocks formatted as JSON files list."""
    text = r"""
    ```json
    {
        "files": [
            {"path": "settings.ini", "content": "[default]\nmode=fast"}
        ]
    }
    ```
    """
    edits = WorkspaceEditor.extract_file_edits(text)
    assert len(edits) == 1
    assert edits[0][0] == "settings.ini"
    assert "mode=fast" in edits[0][1]


def test_apply_edits_calls_broker():
    """Calls broker.write_file for each extracted file."""
    text = """
    ### File: hello.txt
    ```text
    Hello World!
    ```
    """
    mock_broker = MagicMock()
    modified = WorkspaceEditor.apply_edits(text, mock_broker)

    assert modified == ["hello.txt"]
    mock_broker.write_file.assert_called_once_with("hello.txt", "Hello World!")
