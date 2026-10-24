"""run_all.py --forms passthrough to run_data.py (packet 39, AC10)."""

import contextlib

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


def _commands(monkeypatch, argv, fail_label=None):
    """Run run_all.main with every step faked; return the labels run, in order."""
    labels = []

    def fake_run(label, cmd, skip=False):
        labels.append((label, cmd, skip))
        return label != fail_label

    monkeypatch.setattr(run_all, "_run", fake_run)
    monkeypatch.setattr("sys.argv", ["run_all.py", *argv])
    with contextlib.suppress(SystemExit):
        run_all.main()
    return labels


def test_book_sync_runs_between_layers_3_and_4(monkeypatch):
    labels = [label for label, _, _ in _commands(monkeypatch, [])]
    i = labels.index("Book sync")
    assert labels[i - 1].startswith("Layer 3") and labels[i + 1].startswith("Layer 4")


def test_book_sync_command_and_whatif(monkeypatch):
    steps = {label: (cmd, skip) for label, cmd, skip in _commands(monkeypatch, ["--whatif"])}
    cmd, skip = steps["Book sync"]
    assert cmd[1:] == ["run_execution.py", "--sync"]
    assert skip is False


def test_book_sync_skipped_without_execution(monkeypatch):
    steps = {label: skip for label, _, skip in _commands(monkeypatch, ["--no-execution"])}
    assert steps["Book sync"] is True


def test_book_sync_failure_stops_real_run(monkeypatch):
    labels = [label for label, _, _ in _commands(monkeypatch, [], fail_label="Book sync")]
    assert labels[-1] == "Book sync"


def test_book_sync_failure_continues_whatif(monkeypatch):
    labels = [label for label, _, _ in _commands(monkeypatch, ["--whatif"], fail_label="Book sync")]
    assert any(label.startswith("Layer 4") for label in labels)
