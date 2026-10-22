"""Packet 45: pip-audit runs with no ignores; the packet 36 pillow ignores are gone."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_REQUIREMENTS = ["requirements.txt", "requirements-dev.txt"]


def _pin_in(text: str, package: str) -> str | None:
    match = re.search(rf"^{re.escape(package)}==(\S+)$", text, re.MULTILINE)
    return match.group(1) if match else None


def _pin(requirements: str, package: str) -> str:
    pin = _pin_in((_ROOT / requirements).read_text(), package)
    assert pin, f"{package} not pinned in {requirements}"
    return pin


def _version(pin: str) -> tuple[int, ...]:
    return tuple(int(part) for part in pin.split("."))


def _makefile() -> str:
    return (_ROOT / "Makefile").read_text()


def _audit_lines(makefile: str) -> list[str]:
    """pip-audit recipe lines, with backslash continuations joined."""
    joined = makefile.replace("\\\n", " ")
    return [line for line in joined.splitlines() if "pip-audit" in line]


class TestNoAuditIgnores:
    def test_security_target_has_no_ignores(self):
        audit = _audit_lines(_makefile())
        assert len(audit) == 1
        assert "--ignore-vuln" not in audit[0]
        # An ignore could also arrive through a Makefile variable.
        assert "$(" not in audit[0]

    def test_pillow_ignore_variable_removed(self):
        assert "PILLOW_AUDIT_IGNORES" not in _makefile()


class TestPins:
    def test_pillow_at_fixed_version(self):
        for requirements in _REQUIREMENTS:
            assert _version(_pin(requirements, "pillow")) >= (13,), requirements

    def test_streamlit_allows_pillow_13(self):
        # streamlit 1.66.0 and earlier require pillow<13.
        for requirements in _REQUIREMENTS:
            assert _version(_pin(requirements, "streamlit")) >= (1, 66, 1), requirements

    def test_virtualenv_at_fixed_version(self):
        assert _version(_pin("requirements-dev.txt", "virtualenv")) >= (21, 14, 9)

    def test_shared_pins_agree(self):
        # virtualenv is dev-only (via pre-commit), so it is not in requirements.txt.
        for package in ("pillow", "streamlit", "packaging", "certifi"):
            assert _pin("requirements.txt", package) == _pin("requirements-dev.txt", package), (
                package
            )

    def test_certifi_at_fixed_version(self):
        for requirements in _REQUIREMENTS:
            assert _version(_pin(requirements, "certifi")) >= (2026, 10, 19), requirements


class TestVersionParsing:
    def test_versions_compare_numerically(self):
        # String comparison would put 2026.9.1 after 2026.10.19, and 21.14.12 before 21.14.9.
        assert _version("2026.9.1") < (2026, 10, 19)
        assert _version("12.3.0") < (13,) <= _version("13.0.0")
        assert _version("21.14.12") >= (21, 14, 9)


class TestAdversarial:
    def test_ignore_on_continuation_line_is_caught(self):
        text = "security:\n\tpip-audit -r requirements.txt \\\n\t\t--ignore-vuln GHSA-xxxx\n"
        assert "--ignore-vuln" in _audit_lines(text)[0]

    def test_similarly_named_package_does_not_count(self):
        text = "pillow-heif==1.0.0\npillow==12.3.0\n"
        assert _pin_in(text, "pillow") == "12.3.0"
        assert _pin_in("pillow-heif==1.0.0\n", "pillow") is None

    def test_commented_pin_does_not_count(self):
        assert _pin_in("#   pillow==13.0.0\n", "pillow") is None
