import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: tests that open a real Target browser session"
    )
