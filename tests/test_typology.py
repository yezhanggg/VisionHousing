"""Places like this one: seeded k-means, the elbow rule, rule-made names, neighbours, section."""

from __future__ import annotations

import shutil

import numpy as np
import pandas as pd
import pytest

from visionhousing.data.pack import TABLES, build_dictionary
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.features import typology as ty
from visionhousing.profile import load_profile_context
from visionhousing.profile.registry import build_section

RULES = ty.load_rules()


def blobs(sizes=(30, 30, 30), seed=1) -> tuple[np.ndarray, np.ndarray]:
    """Three well-separated groups in 2-D."""
    rng = np.random.default_rng(seed)
    centres = np.array([[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])
    x = np.vstack([c + rng.normal(0, 0.5, (n, 2)) for c, n in zip(centres, sizes, strict=True)])
    truth = np.repeat(np.arange(len(sizes)), sizes)
    return x, truth


def tl_tables() -> dict[str, pd.DataFrame]:
    return {
        n: pd.read_csv(TESTLAND_DIR / f"{n}.csv", dtype={"geoid": str, "county": str})
        for n in ("counties", "tracts", "permits_county_year")
    }


# --------------------------------------------------------------------------- k-means


def test_kmeans_finds_separated_groups_and_is_seeded():
    x, truth = blobs()
    fit = ty.kmeans(x, 3, seed=7, n_init=10)
    for g in range(3):  # every true group lands in exactly one fitted group
        assert len(set(fit.labels[truth == g])) == 1
    assert len(set(fit.labels)) == 3
    again = ty.kmeans(x, 3, seed=7, n_init=10)
    assert np.array_equal(fit.labels, again.labels) and fit.inertia == again.inertia
    manual = sum(((x[fit.labels == j] - fit.centres[j]) ** 2).sum() for j in range(3))
    assert fit.inertia == pytest.approx(manual)


def test_kmeans_rejects_impossible_k():
    with pytest.raises(ValueError):
        ty.kmeans(np.zeros((2, 2)), 3, seed=1)


def test_elbow_rule_picks_three_for_three_groups():
    x, _ = blobs()
    k, fits = ty.choose_k(x, [2, 6], 0.05, seed=3, n_init=5)
    assert k == 3
    total = ((x - x.mean(axis=0)) ** 2).sum()
    gains = {j: (fits[j].inertia - fits[j + 1].inertia) / total for j in range(2, 6)}
    assert gains[2] >= 0.05 and gains[3] < 0.05
    assert all(fits[j].inertia >= fits[j + 1].inertia for j in range(2, 6))


def test_elbow_rule_caps_k_at_the_number_of_rows():
    k, fits = ty.choose_k(np.arange(8.0).reshape(4, 2), [3, 8], 0.0, seed=1, n_init=2)
    assert max(fits) == 4 and k == 4


def test_nearest_matches_brute_force_and_skips_itself():
    x, _ = blobs((5, 5, 5))
    near = ty.nearest(x, 3, chunk=4)
    d = ((x[:, None] - x[None]) ** 2).sum(axis=2)
    np.fill_diagonal(d, np.inf)
    assert np.array_equal(near, np.argsort(d, axis=1, kind="stable")[:, :3])
    assert not (near == np.arange(len(x))[:, None]).any()


def test_zscores_fill_missing_with_the_median_and_log_when_asked():
    f = pd.DataFrame({"a": [1.0, 2.0, np.nan, 4.0], "b": [10.0, 100.0, 1000.0, 10000.0],
                      "flat": [5.0] * 4})  # fmt: skip
    z, filled = ty.zscores(f, {"b": "log"})
    assert list(filled) == [0, 0, 1, 0]
    assert "flat" not in z.columns  # no spread, no information
    assert z["a"].iloc[2] == pytest.approx(
        (2.0 - f["a"].fillna(2.0).mean()) / f["a"].fillna(2.0).std(ddof=0)
    )
    assert z.mean().abs().max() < 1e-12


# --------------------------------------------------------------------------- names


def test_names_come_from_rules():
    centres = pd.DataFrame(
        {"permits_per_1000": [1.0, -1.0, 0.0], "population_change": [1.0, -1.0, 0.0],
         "median_rent": [1.0, -1.0, 0.0], "median_value": [1.0, -1.0, 0.0],
         "renter_share": [0.0, 0.0, 2.0]},
        index=[1, 2, 3],
    )  # fmt: skip
    names = ty.name_groups(centres, pd.Series({1: 120.0, 2: 10.0, 3: 2000.0}), RULES, "county")
    assert names == {1: "Suburban, growing, higher-cost", 2: "Rural, slow-growth, lower-cost",
                     3: "Big-city, steady"}  # fmt: skip
    # a tract needs a higher density for the same word
    tract = ty.name_groups(centres.rename(columns={"permits_per_1000": "new_homes_share"}),
                           pd.Series({1: 120.0, 2: 10.0, 3: 2000.0}), RULES, "tract")  # fmt: skip
    assert tract[1].startswith("Suburban") and tract[2].startswith("Rural")


def test_equal_names_add_the_most_unusual_feature():
    centres = pd.DataFrame(
        {"new_homes_share": [0.0, 0.0], "renter_share": [1.5, -0.2], "vacancy_rate": [0.1, 2.0]},
        index=[1, 2],
    )
    names = ty.name_groups(centres, pd.Series({1: 50.0, 2: 50.0}), RULES, "tract")
    assert names == {
        1: "Small-town, steady, more renters",
        2: "Small-town, steady, more empty homes",
    }


def test_traits_list_the_most_unusual_features():
    centre = pd.Series({"renter_share": 1.2, "vacancy_rate": -0.8, "share_65plus": 0.1})
    assert ty.traits(centre, RULES) == ["more renters", "few empty homes"]


# --------------------------------------------------------------------------- tables


def test_typology_tables_on_testland():
    out = ty.typology_tables(tl_tables())
    tracts = out["typology_tract"]
    assert len(tracts) == 40 and tracts["geoid"].is_unique
    k = int(tracts["k"].iloc[0])
    assert RULES["k_range"][0] <= k <= RULES["k_range"][1]
    assert set(tracts["type_id"]) == set(range(1, k + 1))
    assert (
        tracts.groupby("type_id").size() == tracts.groupby("type_id")["places_in_type"].first()
    ).all()
    for g, similar in zip(tracts["geoid"], tracts["similar"], strict=True):
        ids = similar.split(";")
        assert len(ids) == RULES["n_similar"] and g not in ids and set(ids) <= set(tracts["geoid"])
    assert (tracts["type_name"].str.len() > 0).all()
    again = ty.typology_tables(tl_tables())
    pd.testing.assert_frame_equal(tracts, again["typology_tract"])
    county = out["typology_county"]
    assert len(county) == 4 and county["similar"].str.count(";").eq(2).all()


def test_densest_group_is_numbered_first():
    t = ty.typology_tables(tl_tables())["typology_tract"]
    by_group = t.groupby("type_id")["housing_density"].apply(lambda s: np.log1p(s).mean())
    assert list(by_group.sort_values(ascending=False).index) == sorted(by_group.index)


def test_dictionary_describes_every_typology_column():
    out = ty.typology_tables(tl_tables())
    assert set(out) <= set(TABLES)
    d = build_dictionary(out, "TL")
    described = {(c["table"], c["name"]) for c in d["columns"]}
    for name, frame in out.items():
        for col in frame.columns:
            assert (name, col) in described


def test_too_few_places_gives_no_table():
    tables = tl_tables()
    tables["counties"] = tables["counties"].head(2)
    assert "typology_county" not in ty.typology_tables(tables)


# --------------------------------------------------------------------------- section


def test_section_on_testland_county_and_tract():
    ctx = load_profile_context("TL")
    sec = build_section("typology", ctx, "99001")
    assert sec.status == "ok"
    name = sec.fact("typology.type_name")
    assert name.value and "k-means" in name.note and "elbow" in name.note
    similar = sec.fact("typology.similar")
    assert len(similar.text.split(", ")) == 3 and "County" in similar.text
    assert "most like it" in sec.summary
    tract = build_section("typology", ctx, "99001000100")
    assert tract.status == "ok" and "Census tract" in tract.fact("typology.similar").text


def test_section_reads_the_pack_table_when_present(tmp_path):
    folder = tmp_path / "TL"
    shutil.copytree(TESTLAND_DIR, folder)
    table = ty.typology_tables(tl_tables())["typology_county"]
    table["type_name"] = "From the pack table"
    table.to_csv(folder / "typology_county.csv", index=False)
    ctx = load_profile_context("TL", folder=folder)
    assert build_section("typology", ctx, "99003").fact("typology.type_name").value == (
        "From the pack table"
    )
