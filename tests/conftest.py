"""Shared pytest fixtures."""

import sys
from pathlib import Path

import pytest
import yaml

# Ensure project root is on sys.path so `data.*` imports resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.db import get_engine, initialise_schema


@pytest.fixture
def tmp_engine(tmp_path):
    """SQLAlchemy Engine backed by a throw-away SQLite file."""
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialise_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tmp_db(tmp_engine):
    """Open SQLAlchemy Connection for use in tests; auto-closed on teardown."""
    conn = tmp_engine.connect()
    yield conn
    conn.close()


@pytest.fixture
def config():
    cfg = Path(__file__).parent.parent / "config.yaml"
    with open(cfg) as f:
        return yaml.safe_load(f)
