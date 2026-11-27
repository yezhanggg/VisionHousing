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
    "docs/POLICY_SCENARIO_COMPARISON.md",
    "docs/TECHNICAL_APPENDIX.md",
    "docs/modules/baseline.md",
    "docs/methods/fees.md",
    "src/visionhousing/data/sources.yaml",
    "assumptions/parameters.yaml",
    "assumptions/fee_schedules.yaml",
    "assumptions/law/municipal_rules.yaml",
    "assumptions/law/local_readings.yaml",
    "assumptions/calibration",
    "uv.lock",
    "web/package.json",
    "web/package-lock.json",
    "web/public/data/PA/runs",
    "web/public/data/PA/pool.json",
    "web/public/data/PA/report-card.json",
    "web/public/data/PA/validation.json",
    "web/public/data/VT/runs",
    "web/public/data/VT/pool.json",
    "web/public/data/VT/report-card.json",
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
    # The full tables (appendix) and the ledger fingerprint (overview) both follow the ledger.
    assert g.stale(repo) == ["docs/MODEL_AND_ASSUMPTIONS.md", "docs/TECHNICAL_APPENDIX.md"]


def test_key_assumptions_are_all_in_the_ledger(repo):
    """The Overview's key-assumption table names only real ledger entries, in its fixed order."""
    text = g.key_assumptions(repo)
    assert "Not in the ledger" not in text
    positions = [text.index(f"`{pid}`") for pid in g.KEY_ASSUMPTIONS]
    assert positions == sorted(positions)
    assert "`adu.unit_size_sqft`" in text  # the ADU size range the Overview's wording refers to


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
    line = g.mark_public_urls(f"| [x]({url}) |")
    assert not g._scan.scan_text("doc", line, [])  # a long address passes the scan
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    flagged = g.mark_public_urls(f"| [x]({url}) | {key} |")
    assert g.ALLOW_URL not in flagged  # a real key is never excused
    assert g._scan.scan_text("doc", flagged, [])


def test_sparse_state_tables_come_from_the_saved_runs(repo):
    """Section 3.4 is built from the static-site runs; without them it says how to make them."""
    text = g.sparse_state_comparison(repo)
    assert "| Accessory dwelling units |" in text and "VT width / PA width" in text
    assert "No town-rule index for VT" in g.sparse_state_inputs(repo)
    shutil.rmtree(repo / "web" / "public" / "data" / "VT")
    assert "make export-static" in g.sparse_state_comparison(repo)
    assert "make export-static" in g.sparse_state_inputs(repo)


def test_uv_groups_name_optional_extras():
    lock = {
        "package": [
            {"name": "visionhousing", "optional-dependencies": {"geo": [{"name": "s"}]}},
            {"name": "s", "dependencies": [{"name": "n"}]},
            {"name": "n"},
        ]
    }
    assert g.uv_groups(lock) == {"s": ["geo (extra)"], "n": ["geo (extra)"]}


def _rec(p10, p50, p90):
    return {"p10": p10, "p50": p50, "p90": p90}


def test_results_blocks_read_the_current_run_format(tmp_path):
    """A run saved under the outcome contract (as-of date, gross/net, years) fills every block."""
    import json

    runs = tmp_path / "web" / "public" / "data" / "PA" / "runs"
    runs.mkdir(parents=True)
    years = {"years": [2027, 2028], "state": [_rec(1, 2, 3), _rec(4, 5, 6)]}
    base = {"seed": 0, "draws": 10, "as_of": "2026-11-24", "baseline": _rec(10, 20, 30),
            "reform_results": {}, "package": None,
            "annual": {"baseline": years, "package": None}}  # fmt: skip
    mm = {"title": "Missing middle", "added": _rec(1, 2, 3), "pct_change": _rec(0.1, 0.2, 0.3),
          "gross_net": {"net_stock": _rec(0, 1, 2)}}  # fmt: skip
    package = {**base, "reform_results": {"missing_middle": mm},
               "package": {"combined": _rec(1, 2, 3), "pct_change": _rec(0.1, 0.2, 0.3),
                           "sum_of_parts": _rec(1, 2, 3), "overlap": _rec(0, 0, 0),
                           "interaction": _rec(0, 0, 0)},
               "annual": {"baseline": years, "package": years}}  # fmt: skip
    (runs / "baseline.json").write_text(json.dumps(base))
    (runs / "missing_middle.json").write_text(json.dumps({**base, "reform_results": {
        "missing_middle": mm}}))  # fmt: skip
    (runs / "package.json").write_text(json.dumps(package))
    head = g.headline_table("PA")(tmp_path)
    assert "Frozen as of 2026-11-24" in head and "before the current outcome rules" not in head
    assert (
        "| Missing middle | 1 to 3 (middle 2) | 0.1% to 0.3% (middle 0.2%) | 0 to 2 (middle 1) |"
        in head
    )
    assert "| 2028 | 4 to 6 (middle 5) | 4 to 6 (middle 5) |" in g.annual_table("PA")(tmp_path)
    assert "Equals all five together | 1 to 3 (middle 2)" in g.package_waterfall("PA")(tmp_path)
    assert "No saved runs for VT" in g.headline_table("VT")(tmp_path)
