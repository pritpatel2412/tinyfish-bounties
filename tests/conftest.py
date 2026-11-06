"""
tests/conftest.py
Global pytest fixtures and environment configuration.
"""
import os
import pytest

# Ensure offline test defaults during import/collection
os.environ.setdefault("STORE_BACKEND", "sqlite")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("TINYFISH_API_KEY", "mock-test-key")
os.environ.setdefault("GROQ_API_KEY", "mock-test-key")

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Ensures all test runs use SQLite and test mode."""
    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("TINYFISH_API_KEY", "mock-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "mock-test-key")
