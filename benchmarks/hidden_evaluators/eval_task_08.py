"""Hidden acceptance evaluator for TASK-08: TokenBucket test generation coverage."""

import inspect
import pytest


def test_hidden_generated_tests_present_and_pass():
    # Import the generated test module
    import tests.test_rate_limiter as gen_tests
    
    functions = [
        obj for name, obj in inspect.getmembers(gen_tests, inspect.isfunction)
        if name.startswith("test_")
    ]
    # Agent must have generated at least 2 distinct unit test functions
    assert len(functions) >= 1, "Agent failed to generate test functions in tests/test_rate_limiter.py"
    
    # Execute each generated test function
    for func in functions:
        func()
