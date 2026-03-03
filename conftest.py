"""Shared pytest fixtures for perfid tests.

Provides a short tmp_path to work around the macOS Unix socket path
length limit (~104 chars). GPG agent sockets in deep pytest temp
directories exceed this limit and fail with "No agent running".
"""

import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_path(request):
    """Override pytest's tmp_path with a shorter path for GPG tests."""
    d = tempfile.mkdtemp(prefix="pf-")
    yield __import__("pathlib").Path(d)
    shutil.rmtree(d, ignore_errors=True)
