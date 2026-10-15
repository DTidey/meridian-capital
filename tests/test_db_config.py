"""Tests for packet 32: database URL from .env only, docs for the remote server, utc_now."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from data._utils import utc_now
from data.db import database_url, redacted_url

_ROOT = Path(__file__).resolve().parent.parent
_ENTRY_POINTS = [
    "run_data.py",
    "run_scoring.py",
    "run_transcripts.py",
    "run_analysis.py",
    "run_portfolio.py",
    "run_risk_check.py",
    "run_execution.py",
    "run_reporting.py",
    "dashboard/app.py",
]
_LIVING_DOCS = [
    "README.md",
    "CLAUDE.md",
    ".env.example",
    "docs/specification.md",
    "docs/architecture.md",
    "docs/run_dashboard.md",
    "docs/alpaca_paper_trading.md",
]
# Living docs with no database setup section: checked for stale content only
_OTHER_LIVING_DOCS = ["docs/erd.md", "docs/run_all.md"]
# scheme://user:password@ with a literal password (placeholders in <...> are allowed)
_URL_WITH_PASSWORD = re.compile(r"://[^\s:/@<>]+:(?!<)[^\s@/<>]+@")


class TestDatabaseUrl:
    def test_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@h:5432/meridian")
        assert database_url() == "postgresql+psycopg2://u:p@h:5432/meridian"

    def test_unset_raises(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match=r"DATABASE_URL.*\.env"):
            database_url()

    def test_blank_raises(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "   ")
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            database_url()

    def test_surrounding_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", " sqlite:///x.db\n")
        assert database_url() == "sqlite:///x.db"


class TestRedactedUrl:
    def test_password_masked(self):
        url = "postgresql+psycopg2://meridian:s3cret@db.example:5432/meridian"
        out = redacted_url(url)
        assert "s3cret" not in out
        assert out == "postgresql+psycopg2://meridian:***@db.example:5432/meridian"

    def test_url_without_password_unchanged(self):
        assert redacted_url("sqlite:///cache/meridian.db") == "sqlite:///cache/meridian.db"

    def test_password_with_special_characters_masked(self):
        out = redacted_url("postgresql://u:p%40ss%3Aw0rd@h/db")
        assert "p%40ss" not in out and "p@ss" not in out


class TestEntryPoints:
    @pytest.mark.parametrize("path", _ENTRY_POINTS)
    def test_entry_points_use_helper(self, path):
        src = (_ROOT / path).read_text()
        assert "database_url()" in src
        assert not re.search(r"""["']DATABASE_URL["']""", src)
        assert not re.search(r"""["']database["']""", src)
        assert "sqlite:///" not in src

    @pytest.mark.parametrize("path", _ENTRY_POINTS)
    def test_logged_urls_redacted(self, path):
        src = (_ROOT / path).read_text()
        for line in src.splitlines():
            if re.search(r"(logger\.\w+|print)\(.*\b(db_url|url)\b", line):
                assert "redacted_url(" in line, line
            assert not re.search(r"(logger\.\w+|print)\(.*(engine\.url|str\(engine)", line), line


class TestConfigFile:
    def test_no_database_section(self):
        cfg = yaml.safe_load((_ROOT / "config.yaml").read_text())
        assert "database" not in cfg

    def test_no_connection_url(self):
        text = (_ROOT / "config.yaml").read_text()
        assert not re.search(r"postgres(ql)?(\+\w+)?://", text)
        assert not _URL_WITH_PASSWORD.search(text)


class TestExecutionDotenv:
    def test_loads_env_from_script_dir_not_cwd(self, tmp_path, monkeypatch):
        import run_execution

        script_dir = tmp_path / "repo"
        script_dir.mkdir()
        (script_dir / ".env").write_text("MERIDIAN_TEST_DOTENV=from_root\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / ".env").write_text("MERIDIAN_TEST_DOTENV=from_cwd\n")
        monkeypatch.setattr(run_execution, "__file__", str(script_dir / "run_execution.py"))
        monkeypatch.chdir(elsewhere)
        monkeypatch.delenv("MERIDIAN_TEST_DOTENV", raising=False)

        run_execution._load_dotenv()
        assert os.environ["MERIDIAN_TEST_DOTENV"] == "from_root"

    def test_quoted_value_unquoted(self, tmp_path, monkeypatch):
        import run_execution

        (tmp_path / ".env").write_text('MERIDIAN_TEST_DOTENV="sqlite:///x.db"\n')
        monkeypatch.setattr(run_execution, "__file__", str(tmp_path / "run_execution.py"))
        monkeypatch.delenv("MERIDIAN_TEST_DOTENV", raising=False)

        run_execution._load_dotenv()
        assert os.environ["MERIDIAN_TEST_DOTENV"] == "sqlite:///x.db"


class TestDocs:
    def test_compose_file_deleted(self):
        assert not (_ROOT / "docker-compose.yml").exists()

    @pytest.mark.parametrize("path", _LIVING_DOCS + _OTHER_LIVING_DOCS)
    def test_no_docker(self, path):
        assert "docker" not in (_ROOT / path).read_text().lower()

    @pytest.mark.parametrize("path", _LIVING_DOCS + _OTHER_LIVING_DOCS)
    def test_no_url_with_password(self, path):
        assert not _URL_WITH_PASSWORD.search((_ROOT / path).read_text())

    @pytest.mark.parametrize("path", _LIVING_DOCS)
    def test_database_url_documented(self, path):
        assert "DATABASE_URL" in (_ROOT / path).read_text()

    def test_backlog_item_removed(self):
        assert "utcnow" not in (_ROOT / "docs/backlog.md").read_text()


class TestUtcNow:
    def test_naive_and_current(self):
        now = utc_now()
        assert now.tzinfo is None
        aware = datetime.now(UTC).replace(tzinfo=None)
        assert abs((aware - now).total_seconds()) < 1

    def test_isoformat_has_no_offset(self):
        assert "+" not in utc_now().isoformat(timespec="seconds")

    @pytest.mark.parametrize("folder", ["data", "tests"])
    def test_no_utcnow_left(self, folder):
        for path in (_ROOT / folder).glob("*.py"):
            assert not re.search(r"\butcnow\(", path.read_text()), path.name
