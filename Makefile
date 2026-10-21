PYTHON ?= python3

# Pillow advisories fixed only in 13.0.0, which streamlit's pillow<13 cap blocks (packet 36).
# Nothing here opens untrusted images. Remove once pillow is pinned at 13 or later;
# tests/test_audit_ignores.py fails until then.
PILLOW_AUDIT_IGNORES := \
    GHSA-p4wg-p73j-c528 \
    GHSA-65qm-jvmh-6pqg \
    GHSA-58r4-6rv3-gfh8 \
    GHSA-pchr-8j38-3x4p \
    GHSA-q45j-58j2-45rg \
    GHSA-462x-pgg6-55fp \
    GHSA-c6hw-5grf-ff96 \
    GHSA-vx6w-vv9f-6gvg

.PHONY: venv sync compile lint test security precommit

venv:
	$(PYTHON) -m venv .venv

compile:
	. .venv/bin/activate && $(PYTHON) -m pip install -U pip setuptools wheel pip-tools
	. .venv/bin/activate && pip-compile requirements.in -o requirements.txt
	. .venv/bin/activate && pip-compile requirements-dev.in -o requirements-dev.txt

sync:
	. .venv/bin/activate && $(PYTHON) -m pip install -U pip setuptools wheel pip-tools
	. .venv/bin/activate && pip-sync requirements.txt requirements-dev.txt

lint:
	. .venv/bin/activate && ruff check .
	. .venv/bin/activate && ruff format --check .

test:
	. .venv/bin/activate && pytest

security:
	. .venv/bin/activate && bandit -q -r analysis data execution factors portfolio reporting risk dashboard .github/scripts
	. .venv/bin/activate && XDG_CACHE_HOME=/tmp/.cache pip-audit --no-deps --disable-pip -r requirements.txt -r requirements-dev.txt \
		$(addprefix --ignore-vuln ,$(PILLOW_AUDIT_IGNORES))

precommit:
	. .venv/bin/activate && pre-commit run --all-files
