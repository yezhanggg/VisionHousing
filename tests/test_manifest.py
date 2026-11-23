"""Manifest and provenance tests: round trip, redaction, stable hashes and provenance rows."""

from __future__ import annotations

import hashlib
import json

import pytest

from visionhousing.data import manifest as m
from visionhousing.data.registry import load_registry, parse_registry

FAKE_KEY = "fakekey0123456789"


def write(path, data: bytes):
    path.write_bytes(data)
    return path


def test_entry_for_file_measures_checksum_and_size(tmp_path):
    f = write(tmp_path / "co2024a.txt", b"permits")
    e = m.entry_for_file("bps_county_annual", "https://x.test/co2024a.txt", f, "2024")
    assert e.sha256 == hashlib.sha256(b"permits").hexdigest()
    assert e.size == 7 and e.file == "co2024a.txt"
    assert e.accessed.endswith("+00:00")


def test_file_is_relative_to_root_and_never_absolute(tmp_path):
    (tmp_path / "raw").mkdir()
    f = write(tmp_path / "raw" / "a.txt", b"a")
    assert m.entry_for_file("s", "https://x.test/a", f, "v", root=tmp_path).file == "raw/a.txt"
    elsewhere = tmp_path.parent / "elsewhere"
    assert m.entry_for_file("s", "https://x.test/a", f, "v", root=elsewhere).file == "a.txt"


def test_url_key_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_KEY)
    f = write(tmp_path / "rows.json", b"[]")
    e = m.entry_for_file("acs5_api", f"https://api.test/acs5?get=A&key={FAKE_KEY}", f, "2024")
    assert FAKE_KEY not in e.url and "key=REDACTED" in e.url
    path = m.save_manifest([e], tmp_path / "manifest.json")
    assert FAKE_KEY not in path.read_text(encoding="utf-8")


def test_round_trip_is_exact_and_sorted(tmp_path):
    a = write(tmp_path / "a.txt", b"aaa")
    b = write(tmp_path / "b.txt", b"bbb")
    entries = [
        m.entry_for_file(
            "zeta", "https://x.test/b", b, "2020", accessed="2026-11-22T10:00:00+00:00"
        ),
        m.entry_for_file(
            "alpha", "https://x.test/a", a, "2024", accessed="2026-11-21T09:00:00+00:00"
        ),
    ]
    path = tmp_path / "data" / "manifest.json"
    m.save_manifest(entries, path)
    loaded = m.load_manifest(path)
    assert loaded == sorted(entries, key=lambda e: e.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert set(data["entries"][0]) == {"id", "url", "sha256", "size", "accessed", "vintage", "file"}
    assert not list(path.parent.glob("*.part"))


def test_missing_manifest_is_empty_and_bad_one_fails(tmp_path):
    assert m.load_manifest(tmp_path / "none.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": 99, "entries": []}', encoding="utf-8")
    with pytest.raises(m.ManifestError, match="schema"):
        m.load_manifest(bad)
    bad.write_text('{"schema": 1, "entries": [{"id": "x"}]}', encoding="utf-8")
    with pytest.raises(m.ManifestError, match="bad entry"):
        m.load_manifest(bad)
    with pytest.raises(m.ManifestError, match="sha256"):
        m.ManifestEntry("x", "https://x.test", "nothex", 1, "2026-11-22", "v")


def test_record_replaces_the_same_file(tmp_path):
    path = tmp_path / "manifest.json"
    f = write(tmp_path / "a.txt", b"one")
    m.record(m.entry_for_file("s", "https://x.test/a", f, "v1"), path)
    write(f, b"two")
    entries = m.record(m.entry_for_file("s", "https://x.test/a", f, "v2"), path)
    assert len(entries) == 1 and entries[0].vintage == "v2"
    assert entries[0].sha256 == hashlib.sha256(b"two").hexdigest()


def test_manifest_hash_ignores_access_time(tmp_path):
    f = write(tmp_path / "a.txt", b"same")
    e1 = m.entry_for_file("s", "https://x.test/a", f, "v", accessed="2026-01-01T00:00:00+00:00")
    e2 = m.entry_for_file("s", "https://x.test/a", f, "v", accessed="2026-11-22T00:00:00+00:00")
    assert m.manifest_hash([e1]) == m.manifest_hash([e2])
    write(f, b"different")
    e3 = m.entry_for_file("s", "https://x.test/a", f, "v")
    assert m.manifest_hash([e3]) != m.manifest_hash([e1])


def test_provenance_rows_cover_every_source_in_order(tmp_path):
    sources = load_registry()
    f = write(tmp_path / "co2024a.txt", b"x")
    entries = [
        m.entry_for_file(
            "bps_county_annual", "https://x.test/1", f, "2024", accessed="2026-11-20T01:00:00+00:00"
        ),
        m.entry_for_file(
            "bps_county_annual", "https://x.test/2", f, "2025", accessed="2026-11-22T01:00:00+00:00"
        ),
    ]
    rows = m.provenance_rows(sources, entries)
    assert [r.id for r in rows] == list(sources)
    bps = rows[0]
    assert bps.access_date == "2026-11-22"
    assert bps.licence.startswith("Public domain") and bps.terms.startswith("https://")
    assert "header" in bps.cleaning and bps.limits
    assert all(r.access_date == m.NOT_FETCHED for r in rows[1:])
    for r in rows:
        assert r.source and r.version and r.licence and r.terms and r.cleaning and r.limits


def test_provenance_row_defaults_cleaning_text():
    sources = parse_registry(
        {
            "sources": [
                {
                    "id": "demo",
                    "name": "Demo",
                    "publisher": "Agency",
                    "url_pattern": "https://example.test/x",
                    "vintage": "2024",
                    "geography": ["state"],
                    "licence": "Public domain",
                    "terms_url": "https://example.test/terms",
                    "parser": "demo.parse",
                    "limits": "None known.",
                    "tier": 1,
                }
            ]
        }
    )
    (row,) = m.provenance_rows(sources, [])
    assert row.source == "Demo (Agency)" and row.cleaning == "None beyond parsing."
    assert row.tier == 1
