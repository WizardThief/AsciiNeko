"""
tests/conftest.py - Shared test fixtures and hermetic test environment helpers.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import tempfile
from typing import Generator

from neko_bot.core.config import Config


def get_free_port() -> int:
    """Finds an available local port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


@contextmanager
def temporary_workspace() -> Generator[Config, None, None]:
    """
    Context manager creating an isolated temporary workspace directory
    for hermetic testing without modifying the user's project files.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_workspace_"))
    try:
        cfg = Config.load(root_dir=temp_dir)
        cfg.port = get_free_port()
        cfg.ensure_dirs()
        yield cfg
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
