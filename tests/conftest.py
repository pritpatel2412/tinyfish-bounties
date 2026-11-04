"""
tests/conftest.py
Global pytest fixtures and environment configuration.
"""
import pytest

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Ensures all test runs use SQLite and test mode."""
    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    monkeypatch.setenv("ENV", "test")
