"""Packet 36: pip-audit ignores for pillow last only while streamlit caps pillow below 13."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = _ROOT / "docs" / "specs" / "36-pillow-certifi-advisories.md"
_REQUIREMENTS = ["requirements.txt", "requirements-dev.txt"]
_SOURCE_DIRS = [
    "analysis",
    "reporting",
    "dashboard",
    "data",
    "factors",
    "portfolio",
    "risk",
    "execution",
]
_GHSA_ID = re.compile(r"^GHSA(-[23456789cfghjmpqrvwx]{4}){3}$")
_PIL_IMPORT = re.compile(r"^\s*(import PIL|from PIL)\b", re.MULTILINE)


def _makefile_variable(text: str, name: str) -> list[str]:
    """Words assigned to a Makefile variable, following backslash continuations."""
    joined = text.replace("\\\n", " ")
    match = re.search(rf"^{name}\s*:?=(.*)$", joined, re.MULTILINE)
    assert match, f"{name} not defined in Makefile"
    return match.group(1).split()


def _pin(requirements: str, package: str) -> str:
    match = re.search(rf"^{package}==(\S+)$", (_ROOT / requirements).read_text(), re.MULTILINE)
    assert match, f"{package} not pinned in {requirements}"
    return match.group(1)


def _version(pin: str) -> tuple[int, ...]:
    return tuple(int(part) for part in pin.split("."))


def _ignores() -> list[str]:
    return _makefile_variable((_ROOT / "Makefile").read_text(), "PILLOW_AUDIT_IGNORES")


class TestIgnoresAreTimeBoxed:
    def test_no_ignores_once_pillow_13_is_pinned(self):
        assert _version(_pin("requirements.txt", "pillow")) < (13,) or not _ignores(), (
            "pillow is now >= 13: remove PILLOW_AUDIT_IGNORES from the Makefile"
        )

    def test_pillow_pins_agree(self):
        assert _pin("requirements.txt", "pillow") == _pin("requirements-dev.txt", "pillow")


class TestIgnoreList:
    def test_ids_are_named_ghsa_advisories(self):
        ids = _ignores()
        assert ids
        assert all(_GHSA_ID.match(i) for i in ids), ids
        assert len(ids) == len(set(ids))

    def test_ids_are_listed_in_spec(self):
        # Only the reviewed pillow advisories; anything else needs its own review and spec.
        listed = set(re.findall(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}", _SPEC.read_text()))
        assert set(_ignores()) <= listed

    def test_security_target_passes_the_ignores(self):
        recipe = (_ROOT / "Makefile").read_text().replace("\\\n", " ")
        audit = [line for line in recipe.splitlines() if "pip-audit" in line]
        assert len(audit) == 1
        assert "$(addprefix --ignore-vuln ,$(PILLOW_AUDIT_IGNORES))" in audit[0]

    def test_no_blanket_ignores(self):
        # Only the named pillow advisories: no other --ignore-vuln on the pip-audit line.
        recipe = (_ROOT / "Makefile").read_text().replace("\\\n", " ")
        audit = next(line for line in recipe.splitlines() if "pip-audit" in line)
        assert audit.count("--ignore-vuln") == 1


class TestExposure:
    def test_source_does_not_import_pil(self):
        # The ignores rest on nothing here opening images; a PIL import needs a fresh review.
        files = [p for d in _SOURCE_DIRS for p in (_ROOT / d).rglob("*.py")]
        files += sorted(_ROOT.glob("run_*.py"))
        assert len(files) > 50, "source directories moved: update _SOURCE_DIRS"
        offenders = [str(p.relative_to(_ROOT)) for p in files if _PIL_IMPORT.search(p.read_text())]
        assert not offenders


class TestCertifi:
    def test_certifi_at_fixed_version(self):
        for requirements in _REQUIREMENTS:
            assert _version(_pin(requirements, "certifi")) >= (2026, 10, 19), requirements


class TestMakefileParser:
    def test_continuations_and_single_line(self):
        text = "A := x \\\n    y \\\n    z\nB := w\n"
        assert _makefile_variable(text, "A") == ["x", "y", "z"]
        assert _makefile_variable(text, "B") == ["w"]

    def test_empty_variable(self):
        assert _makefile_variable("A :=\n", "A") == []

    def test_versions_compare_numerically(self):
        # String comparison would put 2026.9.1 after 2026.10.19.
        assert _version("2026.9.1") < (2026, 10, 19)
        assert _version("12.3.0") < (13,) <= _version("13.0.0")
