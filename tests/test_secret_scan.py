"""Secret scan: planted keys, private keys and home paths are caught; clean files pass.

Planted values are assembled at run time so this file itself stays clean.
"""

import secret_scan as ss

FAKE_CLOUD_KEY = "AKIA" + "Q7XZ3M2KD9TBW4PL"
FAKE_CODE_TOKEN = "gh" + "p_" + "".join(["aB3dE5gH7", "jK9mN1pQ3", "sT5vX7zA9", "cE1gI3kM5"])
RANDOM_TOKEN = "".join(["q8ZrT2vL", "x9Pm4Wk7", "Ns1Bd6Hy", "3Fj0Gc5U", "a8Ee2Ri"])
HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
HOME = "/" + "Users/" + "alice/projects/out.csv"


def make_repo(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    listing = tmp_path / "files.txt"
    listing.write_text("\n".join(files) + "\n", encoding="utf-8")
    return listing


def kinds(hits):
    return sorted({h.kind for h in hits})


def test_catches_each_planted_secret(tmp_path, monkeypatch):
    listing = make_repo(
        tmp_path,
        {
            ".env.example": "CENSUS_API_KEY=\n",
            "a.py": f'key = "{FAKE_CLOUD_KEY}"\n',
            "b.txt": f"token {FAKE_CODE_TOKEN}\n",
            "c.pem.txt": HEADER + "\n",
            "out/report.json": f'{{"path": "{HOME}"}}\n',
            "d.py": f'X = "{RANDOM_TOKEN}"\n',
            "e.sh": "export CENSUS_API_KEY=" + "abcdef0123456789abcdef0123456789abcdef01" + "\n",
        },
    )
    monkeypatch.setenv("VH_FILE_LIST", str(listing))
    hits = ss.scan(tmp_path)
    assert {h.path for h in hits} == {
        "a.py",
        "b.txt",
        "c.pem.txt",
        "out/report.json",
        "d.py",
        "e.sh",
    }
    assert "home-folder path" in kinds(hits)
    assert "private-key header" in kinds(hits)
    assert "value for CENSUS_API_KEY" in kinds(hits)
    assert "high-entropy token" in kinds(hits)
    assert ss.main(["--root", str(tmp_path)]) == 1


def test_previews_never_show_the_secret(tmp_path, monkeypatch):
    listing = make_repo(tmp_path, {"a.py": FAKE_CODE_TOKEN + "\n"})
    monkeypatch.setenv("VH_FILE_LIST", str(listing))
    [hit] = [h for h in ss.scan(tmp_path) if h.kind == "code-hosting token"]
    assert FAKE_CODE_TOKEN not in hit.preview


def test_clean_files_pass(tmp_path, monkeypatch):
    listing = make_repo(
        tmp_path,
        {
            ".env.example": "CENSUS_API_KEY=\n",
            "uv.lock": 'hash = "sha256:' + "ab12" * 16 + '"\n',
            "doc.md": "Install into /" + "Users/<name>/ or /" + "home/user/ as you like.\n"
            "See https://example.org/home/page for details.\n",
            "code.py": "def a_long_but_ordinary_function_name_for_testing():\n    pass\n",
            "allowed.txt": f"{FAKE_CLOUD_KEY}  # secret-scan: allow\n",
        },
    )
    monkeypatch.setenv("VH_FILE_LIST", str(listing))
    assert ss.scan(tmp_path) == []


def test_ignored_files_are_not_read(tmp_path, monkeypatch):
    listing = make_repo(tmp_path, {"ok.txt": "hello\n"})
    (tmp_path / ".env").write_text("CENSUS_API_KEY=" + "x" * 40 + "\n", encoding="utf-8")
    monkeypatch.setenv("VH_FILE_LIST", str(listing))
    assert ss.scan(tmp_path) == []


def test_walk_fallback_skips_tool_folders(tmp_path, monkeypatch):
    monkeypatch.delenv("VH_FILE_LIST", raising=False)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "x.py").write_text(FAKE_CLOUD_KEY, encoding="utf-8")
    (tmp_path / "src.py").write_text(FAKE_CLOUD_KEY, encoding="utf-8")
    monkeypatch.setattr(ss.subprocess, "run", _no_git)
    files = ss.publishable_files(tmp_path)
    assert files == ["src.py"]


def _no_git(*args, **kwargs):
    raise OSError("git not available")


def test_real_tree_is_clean():
    assert ss.scan(ss.ROOT) == []


def test_url_paths_pass_but_url_query_keys_are_caught():
    path_only = f"url: https://example.gov/files/{RANDOM_TOKEN}/data.zip"
    assert not [h for h in ss.scan_text("a.yaml", path_only, []) if "entropy" in h.kind]
    with_query = f"url: https://example.gov/files/data.zip?key={RANDOM_TOKEN}"
    assert [h for h in ss.scan_text("a.yaml", with_query, []) if "entropy" in h.kind]


def test_compact_profile_index_columns_are_not_secrets():
    """A compact profile's index column looks random; only the randomness check is skipped."""
    column = "0123456789" + "abcdefghijklmnopqrstuvwxyz" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    line = '{"cols":{"x":{"i":"' + column + '"}}}'
    assert ss.scan_text("web/public/data/PA/profiles/county-values.json", line, []) == []
    assert ss.scan_text("web/public/data/PA/runs/adu.json", line, [])
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    tracts = "web/public/data/PA/profiles/" + "tracts-42001.json"
    assert ss.scan_text(tracts, key, [])
