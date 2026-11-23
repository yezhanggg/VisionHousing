"""Command line: help lists every command; doctor fails loudly and never shows a key."""

from pathlib import Path

import pytest

from visionhousing import cli


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ["doctor", "fetch", "build", "run", "report", "serve"]:
        assert name in out


@pytest.mark.parametrize("command", ["run"])
def test_stubs_explain_themselves(command, capsys):
    assert cli.main([command]) == 0
    out = capsys.readouterr().out
    assert "not built yet" in out and "will" in out


def test_python_check():
    assert cli.check_python((3, 12, 1)).ok
    assert not cli.check_python((3, 11, 9)).ok


def test_uv_check(monkeypatch):
    monkeypatch.delenv("UV", raising=False)
    assert not cli.check_uv(lambda _: None).ok
    assert cli.check_uv(lambda _: "/opt/uv").ok


def test_disk_check_fails_below_threshold(tmp_path):
    assert cli.check_disk(tmp_path, min_gb=0).ok
    assert not cli.check_disk(tmp_path, min_gb=10**9).ok


def test_key_check_never_prints_the_value():
    secret = "do-not-print-" + "1234567890"
    [check] = cli.check_keys({"CENSUS_API_KEY": secret})
    assert check.ok and check.detail == "present" and secret not in check.detail
    [missing] = cli.check_keys({})
    assert missing.ok and not missing.required and "absent" in missing.detail


def test_write_check(tmp_path):
    assert cli.check_write(tmp_path / "data").ok
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert not cli.check_write(blocker / "data").ok


def test_doctor_exit_codes(capsys):
    good = [cli.Check("a", True, "fine"), cli.Check("k", False, "absent", required=False)]
    assert cli.run_doctor(good) == 0
    bad = [*good, cli.Check("disk", False, "1 GB free")]
    assert cli.run_doctor(bad) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "1 check(s) failed" in out


def test_doctor_runs_end_to_end(tmp_path, capsys):
    code = cli.doctor(tmp_path / "data")
    out = capsys.readouterr().out
    assert "python" in out and "disk" in out and "write access" in out
    assert code in (0, 1)
    assert Path(tmp_path / "data").is_dir()
