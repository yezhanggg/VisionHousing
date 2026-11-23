"""Document table generator: blocks rebuilt in place, deterministic, and --check catches drift."""

import shutil
from pathlib import Path

import pytest

import gen_docs_tables as g

ROOT = Path(__file__).resolve().parent.parent

INPUTS = [
    "DATA_PROVENANCE.md",
    "DISCLOSURES.md",
    "docs/MODEL_AND_ASSUMPTIONS.md",
    "src/visionhousing/data/sources.yaml",
    "assumptions/parameters.yaml",
    "assumptions/calibration",
    "uv.lock",
    "web/package.json",
    "web/package-lock.json",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of the documents and the records they are built from."""
    for rel in INPUTS:
        src, dst = ROOT / rel, tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return tmp_path


def test_replace_block_changes_only_the_block():
    text = "before\n<!-- generated: x -->\nold\n<!-- /generated: x -->\nafter\n"
    new = g.replace_block(text, "x", "new body\n")
    assert new == "before\n<!-- generated: x -->\nnew body\n<!-- /generated: x -->\nafter\n"
    assert g.replace_block(new, "x", "new body") == new


def test_replace_block_needs_both_markers_once():
    with pytest.raises(g.BlockError):
        g.replace_block("<!-- generated: x -->\nno end marker\n", "x", "body")
    twice = "<!-- generated: x -->\n<!-- /generated: x -->\n" * 2
    with pytest.raises(g.BlockError):
        g.replace_block(twice, "x", "body")


def test_cells_are_one_line_with_pipes_escaped():
    assert g.cell("a |b\n  c") == "a \\|b c"
    assert g.cell("") == "-"
    assert g.number(13002700.0) == "13,002,700"
    assert g.number(0.066) == "0.066"


def test_check_passes_after_generation(repo):
    assert g.main(["--root", str(repo)]) == 0
    assert g.main(["--root", str(repo), "--check"]) == 0
    for rel, builders in g.DOCUMENTS.items():
        text = (repo / rel).read_text(encoding="utf-8")
        for name in builders:
            body = g.block_pattern(name).search(text).group(0)
            assert body.count("\n") >= 2, (rel, name)  # every block has content


def test_check_fails_on_a_stale_block(repo, capsys):
    g.main(["--root", str(repo)])
    path = repo / "DISCLOSURES.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(g.replace_block(text, "dependency_table", "hand-typed"), encoding="utf-8")
    assert g.main(["--root", str(repo), "--check"]) == 1
    assert "DISCLOSURES.md" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8").count("hand-typed") == 1  # --check writes nothing


def test_check_fails_when_a_record_changes(repo):
    g.main(["--root", str(repo)])
    ledger = repo / "assumptions" / "parameters.yaml"
    text = ledger.read_text(encoding="utf-8")
    ledger.write_text(text.replace('    label: "', '    label: "Changed ', 1), "utf-8")
    assert g.stale(repo) == ["docs/MODEL_AND_ASSUMPTIONS.md"]


def test_generation_is_deterministic_and_idempotent(repo):
    first = g.render(repo)
    assert g.render(repo) == first
    g.main(["--root", str(repo)])
    after = {p: p.read_text(encoding="utf-8") for p in first}
    assert after == first
    g.main(["--root", str(repo)])
    assert {p: p.read_text(encoding="utf-8") for p in first} == after


def test_text_outside_the_blocks_is_kept(repo):
    before = (repo / "DATA_PROVENANCE.md").read_text(encoding="utf-8")
    g.main(["--root", str(repo)])
    after = (repo / "DATA_PROVENANCE.md").read_text(encoding="utf-8")
    outside = g.block_pattern("provenance_table").sub("", before)
    outside = g.block_pattern("calibration_provenance").sub("", outside)
    rebuilt = g.block_pattern("provenance_table").sub("", after)
    rebuilt = g.block_pattern("calibration_provenance").sub("", rebuilt)
    assert outside == rebuilt


def test_tables_cover_every_record(repo):
    from visionhousing.data.registry import load_registry
    from visionhousing.ledger.loader import load_ledger

    sources = load_registry(g.registry_path(repo))
    provenance = g.provenance_table(repo)
    assert all(f"(`{sid}`)" in provenance for sid in sources)
    assert "not yet fetched" not in provenance  # never depends on a local manifest
    ledger = load_ledger(repo / "assumptions" / "parameters.yaml")
    tables = g.assumption_table_sourced(repo) + g.assumption_table_judgement(repo)
    assert all(f"`{pid}`" in tables for pid in ledger)
    calibration = g.calibration_provenance(repo)
    assert "microsoft_building_footprints" in calibration and "ODbL" in calibration
    browser = g.browser_libraries(repo)
    for lib in ("preact", "htm", "@preact/signals", "maplibre-gl"):
        assert f"| {lib} |" in browser


def test_uv_groups_follow_the_dependency_graph():
    lock = {
        "package": [
            {
                "name": "visionhousing",
                "dependencies": [{"name": "a"}],
                "dev-dependencies": {"dev": [{"name": "b"}]},
            },
            {"name": "a", "dependencies": [{"name": "c"}]},
            {"name": "b", "dependencies": [{"name": "c"}]},
            {"name": "c"},
        ]
    }
    assert g.uv_groups(lock) == {"a": ["run time"], "b": ["dev"], "c": ["run time", "dev"]}


def test_long_public_addresses_are_excused_but_keys_are_not():
    url = "https://example.gov/AbCdEf0123" + "/GhIjKl4567/MnOpQr89" + "/StUvWx/Yz-_aBcD.html"
    assert g.mark_public_urls(f"| [x]({url}) |").endswith(g.ALLOW_URL)
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    assert g.ALLOW_URL not in g.mark_public_urls(f"| [x]({url}) | {key} |")
