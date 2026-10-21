"""run_all.py --forms passthrough to run_data.py (packet 39, AC10)."""

import pytest

import run_all


def _layer1_cmd(monkeypatch, argv: list[str]) -> list[str]:
    """Run run_all.main, stopping after Layer 1; return the Layer 1 command."""
    cmds = []

    def fake_run(label, cmd, skip=False):
        cmds.append(cmd)
        return False  # stop the pipeline after Layer 1

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr("sys.argv", ["run_all.py", *argv])
    with pytest.raises(SystemExit):
        run_all.main()
    assert len(cmds) == 1
    return cmds[0]


def test_forms_forwarded(monkeypatch):
    cmd = _layer1_cmd(monkeypatch, ["--whatif", "--no-13f", "--forms", "4"])
    assert cmd[1] == "run_data.py"
    i = cmd.index("--forms")
    assert cmd[i : i + 2] == ["--forms", "4"]
    assert "--no-13f" in cmd


def test_several_forms_forwarded(monkeypatch):
    cmd = _layer1_cmd(monkeypatch, ["--forms", "4", "8-K"])
    i = cmd.index("--forms")
    assert cmd[i : i + 3] == ["--forms", "4", "8-K"]


def test_no_forms_not_forwarded(monkeypatch):
    cmd = _layer1_cmd(monkeypatch, ["--no-13f"])
    assert "--forms" not in cmd
