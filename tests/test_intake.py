"""State intake on Testland: value states, manifest, readiness and reproduce links.

A feed failure must never read as zero; every manifest row must carry every
field or say why it is blank; the readiness check must give pass / fail /
unknown per step; and a record id must lead back to the run's seed and hashes.
"""

from __future__ import annotations

import json
import shutil

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.cli import main as cli_main
from visionhousing.data import report_card as rc
from visionhousing.engine.context import TESTLAND_DIR
from visionhousing.engine.core import run
from visionhousing.intake import value_state as vs
from visionhousing.intake.manifest import FIELDS, build_manifest, evidence_fractions, takeup_status
from visionhousing.intake.readiness import STATUSES, readiness, to_text
from visionhousing.intake.reproduce import ReproduceError, parse_id, reproduce

API = "/api/v1"
DRAWS = 200


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


@pytest.fixture(scope="module")
def manifest():
    return build_manifest("TL")


# --------------------------------------------------------------------------- value states


def test_value_states_are_six_distinct_words_with_labels():
    assert vs.VALUE_STATES == (
        "true_zero", "missing", "suppressed", "imputed", "not_applicable", "feed_failure",
    )  # fmt: skip
    labels = [vs.LABELS[s] for s in (*vs.VALUE_STATES, vs.OBSERVED)]
    assert len(set(labels)) == len(labels)
    for s in vs.VALUE_STATES:
        assert vs.describe(s)


def test_classify_order_and_a_failed_feed_never_shows_zero():
    assert vs.classify(0) == "true_zero"
    assert vs.classify(None) == "missing"
    assert vs.classify(float("nan")) == "missing"
    assert vs.classify(12.5) == vs.OBSERVED
    assert vs.classify(3, imputed=True) == "imputed"
    assert vs.classify(None, suppressed=True) == "suppressed"
    assert vs.classify(0, applicable=False) == "not_applicable"
    assert vs.classify(0, feed_ok=False) == "feed_failure"
    assert vs.display(0, "feed_failure") != "0"
    assert vs.display(None) == "no data"
    assert vs.display(0) == "0"
    assert vs.display(7, "imputed") == "7 (imputed)"
    assert vs.count_states([0, 1, None], imputed=[False, True, False]) == {
        "observed": 0, "true_zero": 1, "missing": 1, "suppressed": 0, "imputed": 1,
        "not_applicable": 0, "feed_failure": 0,
    }  # fmt: skip
    assert vs.worst(["observed", "imputed", "feed_failure"]) == "feed_failure"


def test_report_card_marks_a_promised_but_absent_table_as_feed_failure(tmp_path):
    folder = tmp_path / "pack"
    shutil.copytree(TESTLAND_DIR, folder)
    files = rc.load_pack_files(folder)
    files["dictionary"]["tables"] = [{"table": "costs_county"}]  # promised, never written
    card = rc.report_card(files)
    by_id = {r["id"]: r for r in card["inputs"]}
    assert by_id["construction_costs"]["value_state"] == "feed_failure"
    assert by_id["permits"]["value_state"] == vs.OBSERVED
    assert by_id["zoning_single_family"]["value_state"] == "missing"
    assert card["value_states"]["feed_failure"] == 1
    md = rc.to_markdown(card)
    row = next(line for line in md.splitlines() if line.startswith("| Construction wage"))
    assert "0%" not in row and vs.LABELS["feed_failure"] in row
    assert "failed to load" in md


def test_report_card_counts_row_states():
    card = rc.report_card(rc.load_pack_files(TESTLAND_DIR))
    permits = next(r for r in card["inputs"] if r["id"] == "permits")
    assert sum(permits["row_states"].values()) > 0
    assert set(permits["row_states"]) == {vs.OBSERVED, *vs.VALUE_STATES}


# --------------------------------------------------------------------------- manifest


def test_every_manifest_row_has_every_field_or_a_value_state(manifest):
    assert manifest["state"] == "TL" and manifest["synthetic"]
    assert manifest["fields"] == list(FIELDS)
    assert manifest["counts"]["data"] >= 1 and manifest["counts"]["assumptions"] >= 10
    for group in manifest["rows"].values():
        for row in group:
            for f in FIELDS:
                if row[f] is None:
                    assert row["field_states"][f] in vs.VALUE_STATES, (row["id"], f)
                else:
                    assert f not in row["field_states"], (row["id"], f)
    json.dumps(manifest)


def test_assumption_rows_name_their_ledger_sources(manifest):
    rows = {r["id"]: r for r in manifest["rows"]["assumptions"]}
    row = rows["adu.takeup_feasible_annual"]
    assert "adu" in row["scenarios"] and row["source_id"]
    assert len(row["version_hash"]) == 64
    assert row["field_states"]["permit_stage"] == "not_applicable"


def test_fractions_on_testland_are_a_true_zero_and_not_applicable(manifest):
    fx = manifest["fractions"]
    assert fx["verified_exposure"]["share"] == 0.0
    assert fx["verified_exposure"]["state"] == "true_zero"
    assert fx["verified_exposure"]["lots_total"] > 0
    for reform in ("adu", "missing_middle"):
        assert fx["transferred_takeup"][reform]["state"] == "not_applicable"


def test_verified_exposure_weights_lots_by_verified_rules(tmp_path):
    folder = tmp_path / "pack"
    shutil.copytree(TESTLAND_DIR, folder)
    (folder / "muni_zoning_rules.csv").write_text(
        "geoid,status,sf_lots\n1,verified,300\n2,draft,700\n3,verified,0\n", "utf-8"
    )
    v = evidence_fractions("TL", folder)["verified_exposure"]
    assert v["share"] == 0.3 and v["lots_verified"] == 300 and v["lots_total"] == 1000


def test_takeup_status_reads_calibration_files_and_transfer_rules():
    pa = takeup_status("PA")["adu"]
    assert pa["adu.already_legal_share"]["status"] == "local"
    assert pa["adu.fit_range_position"]["status"] == "local"
    assert pa["adu.takeup_feasible_annual"]["status"] == "transferred"
    other = takeup_status("WY")["adu"]
    assert other["adu.fit_range_position"]["status"] == "transferred"
    assert "widened" in other["adu.fit_range_position"]["how"]


# --------------------------------------------------------------------------- readiness


def test_readiness_gives_five_steps_with_known_statuses(manifest):
    report = readiness("TL", manifest=manifest)
    assert [s["id"] for s in report["steps"]] == [
        "national_inputs", "current_law", "local_evidence", "benchmark_adapter", "publication",
    ]  # fmt: skip
    for s in report["steps"]:
        assert s["status"] in STATUSES and s["checks"]
        assert all(c["status"] in STATUSES for c in s["checks"])
    by = {s["id"]: s for s in report["steps"]}
    assert by["current_law"]["status"] == "unknown"  # synthetic: no law to read
    assert by["publication"]["status"] == "unknown"
    text = to_text(report)
    assert text.startswith("State readiness: TL") and "5. Publication" in text


def test_readiness_without_a_pack_fails_step_one(tmp_path):
    report = readiness("ZZ", folder=tmp_path / "none")
    assert report["overall"] == "fail"
    assert report["steps"][0]["status"] == "fail"
    assert {s["status"] for s in report["steps"][1:]} == {"unknown"}


def test_state_readiness_command_prints_the_steps(capsys):
    assert cli_main(["state-readiness", "--state", "TL"]) == 0
    out = capsys.readouterr().out
    assert "1. National inputs" in out and "[unknown]" in out


# --------------------------------------------------------------------------- reproduce


def test_parse_id_forms_and_errors():
    assert parse_id("pa:adu:reform_results.adu.added") == ("PA", "adu", "reform_results.adu.added")
    assert parse_id("PA:reform_results.tod.steps.2") == ("PA", "tod", "reform_results.tod.steps.2")
    assert parse_id("VT:baseline_counties.50001")[1] == "baseline"
    assert parse_id("VT:package.combined")[1] == "package"
    for bad in ("nonsense", "P1:adu:x", "PA:Bad Run:x", "PA:adu:x;rm"):
        with pytest.raises(ReproduceError):
            parse_id(bad)


def test_reproduce_reads_seed_and_hashes_from_the_run():
    result = run("TL", ["adu"], seed=3, draws=DRAWS)
    data = result.to_dict()
    out = reproduce("TL:adu:reform_results.adu.added", run_data=data, source="test")
    assert out["seed"] == 3 and out["draws"] == DRAWS and out["reforms"] == ["adu"]
    assert out["hashes"] == data["hashes"]
    assert out["record"]["p50"] == pytest.approx(result.reforms["adu"].added.p50)
    assert "--seed 3 --draws 200" in out["command"]
    assert out["api_request"]["body"]["preset"] == "central"
    assert out["code_version"]
    with pytest.raises(ReproduceError):
        reproduce("TL:adu:reform_results.adu.nothing", run_data=data)


def test_reproduce_reads_a_saved_run_and_flags_overrides(tmp_path):
    data = run("TL", ["fees"], {"fees.fee_cut_share": 0.4}, seed=0, draws=DRAWS).to_dict()
    folder = tmp_path / "TL" / "runs"
    folder.mkdir(parents=True)
    (folder / "fees.json").write_text(json.dumps(data, default=str), "utf-8")
    out = reproduce("TL:fees:reform_results.fees.added", runs_dir=tmp_path)
    assert out["command"] is None and "overrides" in out["command_note"]
    assert out["api_request"]["body"]["overrides"] == {"fees.fee_cut_share": 0.4}
    with pytest.raises(ReproduceError):
        reproduce("TL:tod:reform_results.tod.added", runs_dir=tmp_path)


# --------------------------------------------------------------------------- endpoints


def test_intake_endpoint_answers_for_testland(client):
    reply = client.get(f"{API}/intake/TL")
    assert reply.status == 200
    body = reply.json()
    assert body["state"] == "TL" and body["readiness"]["state"] == "TL"
    assert set(body["rows"]) == {"data", "assumptions", "law", "evidence"}
    assert client.get(f"{API}/intake/Z9").status == 400


def test_reproduce_endpoint_runs_testland_when_nothing_is_saved(client):
    reply = client.get(f"{API}/reproduce?id=TL:adu:reform_results.adu.added")
    assert reply.status == 200
    body = reply.json()
    assert body["state"] == "TL" and body["seed"] == 0 and body["hashes"]["code"]
    assert body["run_source"].startswith("live run")
    assert client.get(f"{API}/reproduce?id=bad").status == 400
    assert client.get(f"{API}/reproduce?id=TL:nosuchrun:baseline").status == 404
    assert client.get(f"{API}/reproduce?id=TL:adu:reform_results.adu.nothing").status == 404
