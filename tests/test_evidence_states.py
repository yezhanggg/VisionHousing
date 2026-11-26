"""Tests for the cross-state comparison records, the completeness matrix and the endpoint.

All offline: the records come from small files written here or from the
repository's own ``assumptions/evidence/states/``; the Philadelphia field check
uses a fake client.
"""

from __future__ import annotations

import csv
import json
from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.evidence.matrix import build_matrix, compact, matrix_summary, write_outputs
from visionhousing.evidence.phl_fields import check_fields
from visionhousing.evidence.records import PanelError, load_panel
from visionhousing.evidence.schema import POLICY_TYPES, STATES, ComparisonRecord

API = "/api/v1"


def record(**kw):
    base = {
        "id": "vt.act_test",
        "kind": "policy",
        "state": "VT",
        "jurisdiction": "statewide",
        "policy_types": ["adu"],
        "title": "A test act",
        "citation": "Act 1 of 2026",
        "url": "https://legislature.vermont.gov/example",
        "enacted_date": "2026-05-01",
        "effective_dates": [{"date": "2026-07-01", "applies_to": "all sections"}],
        "geographic_scope": "every municipality",
        "implementation_status": "legally_effective",
        "identification": "not_applicable",
        "retrieved": "2026-11-24",
        "verified": True,
        "verification_note": "Act page shows signed date and effective date.",
        "sources": [{"label": "Act page", "url": "https://legislature.vermont.gov/example"}],
    }
    base.update(kw)
    return base


def write(folder, name, records=None, **extra):
    body = {"version": 1, **({"records": records} if records is not None else {}), **extra}
    (folder / name).write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------- schema


def test_a_good_record_loads():
    rec = ComparisonRecord.model_validate(record())
    assert rec.main_type == "adu"
    assert rec.effective_dates[0].date == date(2026, 7, 1)


@pytest.mark.parametrize(
    "change, words",
    [
        ({"policy_types": ["zoning_magic"]}, "policy_types"),
        ({"state": "XX", "id": "xx.a"}, "two-letter"),
        ({"id": "pa.wrong_state"}, "must start with 'vt.'"),
        ({"verified": False, "verification_note": ""}, "verification_note"),
        ({"kind": "study"}, "identification"),
        ({"implementation_status": "pending"}, "pending or lapsed"),
        ({"url": "not a url"}, "web address"),
        ({"surprise": 1}, "Extra inputs"),
        ({"sources": []}, "sources"),
        ({"sources": [{"label": "blog", "url": "https://example.org/x", "official": False}]},
         "official source"),
        ({"url": "https://ecode360.com/x",
          "sources": [{"label": "code", "url": "https://ecode360.com/x"}]}, "code-publisher"),
    ],
)  # fmt: skip
def test_bad_records_are_refused_with_a_reason(change, words):
    with pytest.raises(ValidationError, match=words):
        ComparisonRecord.model_validate(record(**change))


def test_an_unverified_record_may_rest_on_a_secondary_page():
    rec = ComparisonRecord.model_validate(
        record(
            verified=False,
            verification_note="Only a news report was found; no official page yet.",
            sources=[{"label": "news", "url": "https://example.org/x", "official": False}],
        )
    )
    assert not rec.verified


# ---------------------------------------------------------------- loading


def test_panel_loads_and_refuses_duplicates_and_misfiled_records(tmp_path):
    write(tmp_path, "vt.yaml", [record()])
    panel = load_panel(tmp_path)
    assert panel.summary()["records"] == 1 and panel.summary()["verified"] == 1
    write(tmp_path, "pa.yaml", [record()])  # a Vermont record in pa.yaml
    with pytest.raises(PanelError, match=r"belongs in vt\.yaml"):
        load_panel(tmp_path)
    (tmp_path / "pa.yaml").unlink()
    write(tmp_path, "studies.yaml", [record()])  # same id twice
    with pytest.raises(PanelError, match="repeat"):
        load_panel(tmp_path)


def test_a_bad_record_names_its_file_and_id(tmp_path):
    write(tmp_path, "vt.yaml", [record(policy_types=["nope"])])
    with pytest.raises(PanelError, match=r"vt.yaml: record vt.act_test"):
        load_panel(tmp_path)


# ---------------------------------------------------------------- matrix


def small_panel(tmp_path):
    write(
        tmp_path,
        "vt.yaml",
        [
            record(),
            record(
                id="vt.pending_bill",
                policy_types=["parking", "tod"],
                enacted_date=None,
                implementation_status="pending",
                verified=False,
                verification_note="Bill page not found on the legislature site.",
            ),
            record(
                id="vt.lapsed_fee_bill",
                policy_types=["fees"],
                enacted_date=None,
                implementation_status="lapsed",
                verification_note="Bill page shows it died in committee.",
            ),
        ],
    )
    write(
        tmp_path,
        "studies.yaml",
        [
            record(
                id="vt.study",
                kind="study",
                policy_types=["adu"],
                implementation_status="not_law",
                identification="descriptive",
            ),
            record(
                id="us.cross_city",
                state="US",
                kind="study",
                implementation_status="not_law",
                identification="matched",
            ),
        ],
    )
    write(
        tmp_path,
        "discovery.yaml",
        entries=[
            {"state": "ME", "policy_type": "adu", "label": "LD 2003 (2022)",
             "discovered_via": "search"},
        ],
        trackers=[
            {"id": "open_states", "name": "Open States", "url": "https://openstates.org",
             "programmatic_use": "unclear", "terms_summary": "Terms not checked in this test.",
             "checked_on": "2026-11-24"},
        ],
    )  # fmt: skip
    return load_panel(tmp_path)


def test_matrix_covers_every_state_and_type_with_the_right_status(tmp_path):
    cells = build_matrix(small_panel(tmp_path))
    assert set(cells) == set(STATES) and len(cells) == 51
    assert all(list(row) == list(POLICY_TYPES) for row in cells.values())
    assert cells["VT"]["adu"]["status"] == "reviewed"
    assert cells["VT"]["adu"]["studies"] == ["vt.study"]
    assert cells["VT"]["parking"]["status"] == "discovered_unreviewed"
    assert cells["VT"]["tod"]["unverified"] == ["vt.pending_bill"]
    assert cells["ME"]["adu"]["status"] == "discovered_unreviewed"
    assert cells["ME"]["adu"]["discovered"] == 1
    assert cells["TX"]["adu"]["status"] == "none_found"
    s = matrix_summary(cells)
    assert s["cells"] == 51 * len(POLICY_TYPES)
    assert cells["VT"]["adu"]["enacted"] == ["vt.act_test"]
    # a verified bill that died is read, but it is not a law: not "reviewed"
    assert cells["VT"]["fees"]["status"] == "reviewed_no_enacted_law"
    assert cells["VT"]["fees"]["verified"] == ["vt.lapsed_fee_bill"]
    assert cells["VT"]["fees"]["enacted"] == []
    assert s["by_status"] == {
        "reviewed": 1,
        "reviewed_no_enacted_law": 1,
        "discovered_unreviewed": 3,
        "none_found": 607,
    }
    assert s["states_with_review"] == ["VT"]
    assert s["states_with_any_reading"] == ["VT"]


def test_a_pending_or_lapsed_bill_alone_never_makes_a_cell_reviewed(tmp_path):
    write(
        tmp_path,
        "nj.yaml",
        [
            record(id="nj.pending", state="NJ", policy_types=["adu"], enacted_date=None,
                   implementation_status="pending", citation="A 1", effective_dates=[]),
            record(id="nj.enacted", state="NJ", policy_types=["parking"],
                   implementation_status="enacted", citation="P.L. 2026, c. 1"),
        ],
    )  # fmt: skip
    write(tmp_path, "discovery.yaml", entries=[], trackers=[])
    cells = build_matrix(load_panel(tmp_path))
    assert cells["NJ"]["adu"]["status"] == "reviewed_no_enacted_law"
    assert cells["NJ"]["parking"]["status"] == "reviewed"
    small = compact(cells)
    assert small["rows"]["NJ"][POLICY_TYPES.index("adu")] == "r"
    assert small["key"]["r"] == "reviewed_no_enacted_law"


def test_a_study_alone_never_makes_a_cell_reviewed(tmp_path):
    cells = build_matrix(small_panel(tmp_path))
    assert all(
        c["status"] != "reviewed" or c["enacted"] for r in cells.values() for c in r.values()
    )


def test_outputs_are_written_and_agree(tmp_path):
    (tmp_path / "in").mkdir()
    panel = small_panel(tmp_path / "in")
    csv_path, json_path = write_outputs(panel, tmp_path / "out")
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 51 * len(POLICY_TYPES)
    small = json.loads(json_path.read_text(encoding="utf-8"))
    assert small["rows"]["VT"][POLICY_TYPES.index("adu")] == "R"
    assert small["rows"]["TX"] == "." * len(POLICY_TYPES)
    for row in rows:
        letter = small["rows"][row["state"]][POLICY_TYPES.index(row["policy_type"])]
        assert small["key"][letter] == row["status"]
    assert compact(build_matrix(panel))["rows"] == small["rows"]


# ---------------------------------------------------------------- the repository's own records


def test_the_repository_records_all_load():
    panel = load_panel()
    s = panel.summary()
    assert s["records"] >= 40
    assert s["verified"] + s["unverified"] == s["records"]
    for r in panel.records:
        assert r.retrieved <= date.today()
        for d in r.effective_dates:
            assert d.date.year >= 1950
    states = {r.state for r in panel.records}
    for st in ("PA", "VT", "MA", "CA", "WA", "OR", "CO", "UT", "CT", "NJ", "TX", "FL", "AZ",
               "MT", "NH", "NC", "MN", "NY"):  # fmt: skip
        assert st in states, st


def test_pending_records_have_no_enactment_and_the_pa_bills_stay_pending():
    by_id = load_panel().by_id()
    pending = [r for r in by_id.values() if r.implementation_status == "pending"]
    assert pending and all(r.enacted_date is None for r in pending)
    pa = [r for r in by_id.values() if r.state == "PA" and r.bill in ("SB 1346", "HB 2186")]
    assert len(pa) == 2 and all(r.implementation_status == "pending" for r in pa)


# ---------------------------------------------------------------- endpoint


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    return AsgiClient(create_app(packs_dir=tmp_path_factory.mktemp("packs")))


def test_endpoint_returns_records_and_matrix(client):
    reply = client.get(f"{API}/evidence/states")
    assert reply.status == 200
    body = reply.json()
    assert body["summary"]["records"] == len(body["records"])
    assert len(body["matrix"]["rows"]) == 51
    assert body["matrix"]["types"] == list(POLICY_TYPES)
    assert body["matrix"]["summary"]["cells"] == 51 * len(POLICY_TYPES)
    vt = client.get(f"{API}/evidence/states?state=vt").json()
    assert vt["records"] and all(r["state"] == "VT" for r in vt["records"])
    ver = client.get(f"{API}/evidence/states?verified=true").json()
    assert all(r["verified"] for r in ver["records"])


def test_endpoint_refuses_a_bad_state(client):
    reply = client.get(f"{API}/evidence/states?state=ZZ")
    assert reply.status == 400 and reply.json()["error"] == "invalid_state"


# ---------------------------------------------------------------- Philadelphia field check


def test_phl_field_check_reads_fields_and_counts_with_a_fake_client():
    asked = []

    def fake(url, params):
        asked.append((url, dict(params)))
        if url.endswith("/query"):
            where = params["where"]
            n = {"1=1": 10, "permitissuedate IS NOT NULL": 9, "denialdate IS NOT NULL": 1}[where]
            return json.dumps({"count": n}).encode()
        return json.dumps(
            {
                "name": "PERMITS",
                "description": "Application information, including ... date of application",
                "fields": [
                    {"name": "permitnumber", "type": "esriFieldTypeString"},
                    {"name": "applicanttype", "type": "esriFieldTypeString"},
                    {"name": "permitissuedate", "type": "esriFieldTypeDate"},
                    {"name": "denialdate", "type": "esriFieldTypeDate"},
                ],
            }
        ).encode()

    out = check_fields(fake, url="https://example.org/layer/0")
    assert out["date_fields"] == ["permitissuedate", "denialdate"]
    assert out["application_like"] == ["applicanttype"]
    assert out["has_application_field"] is False and out["describes_application_date"] is True
    assert out["rows"] == 10 and out["filled"] == {"permitissuedate": 9, "denialdate": 1}
    assert all(p["f"] == "json" for _, p in asked)


def test_spokane_permanent_code_dates_are_kept_apart():
    """Permanent code approved 2023-11-20, in effect 2024-01-01; 2025-03-06 is the follow-up."""
    rec = next(r for r in load_panel().records if r.id == "wa.spokane_boca_2022")
    dates = {e.date: e.applies_to for e in rec.effective_dates}
    assert min(dates) == date(2022, 8, 27)  # the retrospective panel's treatment date
    assert "C36459" in dates[date(2024, 1, 1)] and "2023-11-20" in dates[date(2024, 1, 1)]
    assert "expire" in dates[date(2023, 12, 18)]
    assert "follow-up" in dates[date(2025, 3, 6)]
