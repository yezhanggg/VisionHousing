"""Source registry tests: the shipped file is valid and bad entries fail with their id named."""

from __future__ import annotations

import copy

import pytest
import yaml

from visionhousing.data.registry import (
    REGISTRY_PATH,
    Access,
    RegistryError,
    get_source,
    load_registry,
    parse_registry,
)

GOOD = {
    "id": "demo_source",
    "name": "Demo source",
    "publisher": "Demo agency",
    "url_pattern": "https://example.test/files/demo_{year}.csv",
    "vintage": "2024",
    "geography": ["county"],
    "licence": "Public domain",
    "terms_url": "https://example.test/terms",
    "expected_size": 1000,
    "parser": "demo.parse",
    "limits": "Demo limits.",
    "tier": 0,
}


def registry_with(**changes):
    entry = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            entry.pop(key)
        else:
            entry[key] = value
    return {"sources": [entry]}


def test_shipped_registry_loads_and_covers_the_seed_sources():
    sources = load_registry()
    expected = {
        "bps_county_annual",
        "acs5_api",
        "acs5_summary_file",
        "cb_2020_tract",
        "cb_2020_county",
        "tiger_2020_tabblock",
        "fhfa_hpi_county",
        "fhfa_hpi_tract",
        "freddie_pmms",
        "hud_fmr_county",
        "ntm_stops",
        "pa_hb2186",
        "pa_sb1346",
    }
    assert expected <= set(sources)
    for s in sources.values():
        assert s.url_pattern.startswith("https://") and s.limits and s.licence


def test_shipped_registry_has_no_keys_or_tokens_in_urls():
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    assert "key=" not in text and "token=" not in text


def test_url_patterns_fill():
    sources = load_registry()
    assert (
        sources["bps_county_annual"].url(year=2024)
        == "https://www2.census.gov/econ/bps/County/co2024a.txt"
    )
    blocks = sources["tiger_2020_tabblock"]
    assert blocks.access is Access.RANGE
    assert blocks.url(state_fips="42").endswith("/tl_2020_42_tabblock20.zip")
    assert blocks.member_name(state_fips="42") == "tl_2020_42_tabblock20.dbf"
    assert sources["cb_2020_tract"].url(state_fips="42").endswith("cb_2020_42_tract_500k.zip")


def test_missing_placeholder_names_the_source():
    source = parse_registry(registry_with())["demo_source"]
    with pytest.raises(RegistryError, match="source 'demo_source': url needs year"):
        source.url()


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"tier": 5}, "tier"),
        ({"url_pattern": "http://example.test/x"}, "url_pattern"),
        ({"terms_url": "not a url"}, "terms_url"),
        ({"licence": None}, "licence"),
        ({"parser": "NoDots"}, "parser"),
        ({"geography": []}, "geography"),
        ({"expected_size": -1}, "expected_size"),
        ({"surprise_field": "x"}, "surprise_field"),
        ({"access": "carrier_pigeon"}, "access"),
        ({"access": "range"}, "zip member"),
    ],
)
def test_bad_entry_fails_with_its_id(changes, fragment):
    with pytest.raises(RegistryError) as info:
        parse_registry(registry_with(**changes))
    message = str(info.value)
    assert "source 'demo_source'" in message
    assert fragment in message


def test_entry_without_id_is_reported_by_position():
    data = registry_with(id=None)
    with pytest.raises(RegistryError, match=r"source #1 \(no id\)"):
        parse_registry(data)


def test_duplicate_ids_fail():
    data = {"sources": [GOOD, GOOD]}
    with pytest.raises(RegistryError, match="source 'demo_source': duplicate id"):
        parse_registry(data)


def test_top_level_shape_is_checked():
    with pytest.raises(RegistryError, match="'sources' list"):
        parse_registry({"source": []})
    with pytest.raises(RegistryError, match="mapping of fields"):
        parse_registry({"sources": ["just a string"]})


def test_load_and_get_from_a_file(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(registry_with()), encoding="utf-8")
    assert get_source("demo_source", path).publisher == "Demo agency"
    with pytest.raises(RegistryError, match="source 'nope': not in the registry"):
        get_source("nope", path)
