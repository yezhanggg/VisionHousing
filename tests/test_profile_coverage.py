"""The profile coverage table: what each place-profile section is built from and what it covers.

- Every section of the profile registry has an entry; every source id is a registered
  source, and the section's own module names it (so the table cannot drift from the code).
- Local-only sources carry the counties they exist for, read from the module's ``SOURCES``.
- ``web/js/content/coverage.json`` (what the Place screen reads) is up to date.
"""

from __future__ import annotations

from pathlib import Path

from visionhousing.data.registry import load_registry
from visionhousing.profile import coverage
from visionhousing.profile.registry import SECTIONS

SECTIONS_DIR = Path(coverage.__file__).parent / "sections"


def test_every_section_has_registered_sources_its_module_names():
    reg = load_registry()
    assert set(coverage.SECTION_SOURCES) == {k for k, _ in SECTIONS}
    for key, ids in coverage.SECTION_SOURCES.items():
        code = (SECTIONS_DIR / f"{key}.py").read_text(encoding="utf-8")
        for sid in ids:
            assert sid in reg, f"{key}: {sid} is not in data/sources.yaml"
            assert f'"{sid}"' in code, f"{key}: its module never names {sid}"


def test_table_says_what_each_source_covers():
    table = coverage.coverage_table()["sections"]
    owners = {s["id"]: s for s in table["owners"]["sources"]}
    assert owners["phl_opa_properties"]["counties"] == ["42101"]
    assert "only for counties 42101" in owners["phl_opa_properties"]["covers"]
    assert table["people"]["text"].startswith("Not measured for this place. People is built from")
    assert "published for every state" in table["people"]["text"]
    assert "exist only for some places" in table["owners"]["text"]
    assert table["data_quality"]["sources"] == []
    assert table["data_quality"]["text"] == coverage.NO_SOURCE


def test_browser_copy_is_up_to_date():
    assert coverage.OUT_PATH.read_text(encoding="utf-8") == coverage.render(), (
        "run: uv run python -m visionhousing.profile.coverage"
    )
