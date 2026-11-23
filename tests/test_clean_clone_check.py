"""Clean-clone check: only publishable regular files are copied."""

import clean_clone_check as ccc


def test_copy_skips_symlinks_and_missing(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (src / "secret.txt").write_text("hidden\n", encoding="utf-8")
    (src / "link").symlink_to(src / "secret.txt")
    dest = tmp_path / "dest"
    copied = ccc.copy_files(src, ["pkg/a.py", "link", "gone.txt"], dest)
    assert copied == ["pkg/a.py"]
    assert (dest / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (dest / "link").exists()
    assert not (dest / "secret.txt").exists()


def test_publishable_files_excludes_ignored():
    files = ccc.publishable_files(ccc.ROOT)
    assert "pyproject.toml" in files
    assert not any(f.startswith(("nopush/", ".venv/", ".claude/")) for f in files)
    assert ".env" not in files and "CLAUDE.local.md" not in files


def test_clean_env_drops_active_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere")
    assert "VIRTUAL_ENV" not in ccc.clean_env()
