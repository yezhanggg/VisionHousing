"""The comparisons disk cache: keys, disk hits, a missing drive, safe writes, the budget."""

from __future__ import annotations

import gzip
import json
import logging
import os

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import comparison_cache as cc
from visionhousing.api import create_app
from visionhousing.api.comparison_cache import (
    ComparisonDiskCache,
    cached_comparisons,
    fingerprint,
    part_key,
)
from visionhousing.cli import main
from visionhousing.engine.comparisons import comparisons
from visionhousing.engine.context import load_context
from visionhousing.ledger.loader import ROOT

DRAWS = 60


@pytest.fixture(scope="module")
def ctx():
    return load_context("TL")


@pytest.fixture
def assumptions(tmp_path):
    """A copy of the assumption files, so a test may edit one."""
    folder = tmp_path / "assumptions"
    for src in (ROOT / "assumptions").rglob("*.yaml"):
        dest = folder / src.relative_to(ROOT / "assumptions")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
    return folder


def _counting():
    calls: list[list[str]] = []

    def run(ctx, parts, **kw):
        calls.append(list(parts))
        return comparisons(ctx, parts, **kw)

    return run, calls


def test_key_changes_when_any_input_changes(ctx, assumptions, monkeypatch):
    base = fingerprint(ctx, seed=0, draws=DRAWS, assumptions=assumptions)
    assert base == fingerprint(ctx, seed=0, draws=DRAWS, assumptions=assumptions)
    assert base != fingerprint(ctx, seed=1, draws=DRAWS, assumptions=assumptions)
    assert base != fingerprint(ctx, seed=0, draws=DRAWS + 1, assumptions=assumptions)
    # the pack, the calibration tables and the ledger
    for field in ("data_hash", "calibration_hash"):
        other = type("Ctx", (), {})()
        for name in ("state", "data_hash", "calibration_hash", "ledger"):
            setattr(other, name, getattr(ctx, name))
        setattr(other, field, "0" * 64)
        assert fingerprint(other, seed=0, draws=DRAWS, assumptions=assumptions) != base
    # any assumption file, even one the ledger does not read
    target = next(iter(sorted(assumptions.rglob("*.yaml"))))
    target.write_text(target.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    edited = fingerprint(ctx, seed=0, draws=DRAWS, assumptions=assumptions)
    assert edited != base
    # the code
    monkeypatch.setattr("visionhousing.engine.hashes.code_hash", lambda: "f" * 64)
    assert fingerprint(ctx, seed=0, draws=DRAWS, assumptions=assumptions) != edited
    # the part's own options
    assert part_key(base, "dials") != part_key(base, "matrix")
    assert part_key(base, "dials") != part_key(base, "dials", {"overrides": {"x": 1}})


def test_disk_hit_avoids_recompute_and_equals_fresh(ctx, tmp_path):
    cache = ComparisonDiskCache(tmp_path / "cmp", max_bytes=2**30)
    run, calls = _counting()
    first = cached_comparisons(
        ctx, ["interactions", "matrix"], seed=2, draws=DRAWS, cache=cache, compute=run
    )
    assert calls == [["interactions", "matrix"]]
    shared = fingerprint(ctx, seed=2, draws=DRAWS)
    for part in ("interactions", "matrix"):
        assert (tmp_path / "cmp" / "TL" / shared / f"{part}.json.gz").is_file()
    # a fresh cache object (as after a restart) reads both from disk
    again = ComparisonDiskCache(tmp_path / "cmp", max_bytes=2**30)
    second = cached_comparisons(
        ctx, ["interactions", "matrix"], seed=2, draws=DRAWS, cache=again, compute=run
    )
    assert calls == [["interactions", "matrix"]]
    assert json.dumps(second) == json.dumps(first)
    # only the missing part is computed
    third = cached_comparisons(
        ctx, ["matrix", "ranking"], seed=2, draws=DRAWS, cache=again, compute=run
    )
    assert calls[-1] == ["ranking"]
    assert third["matrix"] == first["matrix"] and "ranking" in third
    # another seed is another key
    cached_comparisons(ctx, ["matrix"], seed=3, draws=DRAWS, cache=again, compute=run)
    assert calls[-1] == ["matrix"]


def test_stale_or_damaged_file_is_recomputed(ctx, tmp_path):
    cache = ComparisonDiskCache(tmp_path / "cmp", max_bytes=2**30)
    run, calls = _counting()
    cached_comparisons(ctx, ["interactions"], seed=0, draws=DRAWS, cache=cache, compute=run)
    path = cache.path("TL", fingerprint(ctx, seed=0, draws=DRAWS), "interactions")
    body = json.loads(gzip.decompress(path.read_bytes()))
    body["key"] = "not-the-key"
    path.write_bytes(gzip.compress(json.dumps(body).encode()))
    cached_comparisons(ctx, ["interactions"], seed=0, draws=DRAWS, cache=cache, compute=run)
    assert len(calls) == 2
    path.write_bytes(b"not gzip")
    out = cached_comparisons(ctx, ["interactions"], seed=0, draws=DRAWS, cache=cache, compute=run)
    assert len(calls) == 3 and "interactions" in out


def test_missing_drive_falls_back_without_disk(ctx, tmp_path, caplog):
    gone = tmp_path / "unplugged"  # a link to a drive that is not mounted
    os.symlink(tmp_path / "no-such-volume", gone)
    cache = ComparisonDiskCache(gone / "comparisons", max_bytes=2**30)
    run, calls = _counting()
    with caplog.at_level(logging.WARNING, logger="visionhousing.api"):
        out = cached_comparisons(
            ctx, ["interactions"], seed=0, draws=DRAWS, cache=cache, compute=run
        )
        cached_comparisons(ctx, ["interactions"], seed=0, draws=DRAWS, cache=cache, compute=run)
    assert "interactions" in out and len(calls) == 2
    assert not (tmp_path / "no-such-volume").exists()
    warnings = [r for r in caplog.records if "disk cache off" in r.getMessage()]
    assert len(warnings) == 1  # logged once, not on every request


def test_atomic_write_leaves_no_partial_file(ctx, tmp_path, monkeypatch):
    cache = ComparisonDiskCache(tmp_path / "cmp", max_bytes=2**30)
    shared = "a" * 32
    assert cache.save("TL", shared, "matrix", {"state": "TL"}, {"x": 1})
    path = cache.path("TL", shared, "matrix")
    assert cache.load("TL", shared, "matrix") == {"header": {"state": "TL"}, "value": {"x": 1}}

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cc.os, "replace", broken)
    assert not cache.save("TL", shared, "matrix", {"state": "TL"}, {"x": 2})
    assert cache.load("TL", shared, "matrix")["value"] == {"x": 1}  # old file intact
    assert not list(path.parent.glob("*.part"))  # the temporary file is removed


def test_budget_removes_oldest_used_first(tmp_path):
    cache = ComparisonDiskCache(tmp_path / "cmp", max_bytes=2**30)
    big = {"v": [str(i) * 7 for i in range(3000)]}  # does not compress to nothing
    for i, part in enumerate(("interactions", "dials", "matrix")):
        cache.save("TL", "b" * 32, part, {}, big)
        os.utime(cache.path("TL", "b" * 32, part), ns=(10**18 + i, 10**18 + i))
    size = cache.path("TL", "b" * 32, "matrix").stat().st_size
    cache.load("TL", "b" * 32, "interactions")  # used last, so kept
    cache.max_bytes = int(size * 2.5)
    cache.trim()
    left = sorted(p.name for p in (tmp_path / "cmp" / "TL" / ("b" * 32)).iterdir())
    assert left == ["interactions.json.gz", "matrix.json.gz"]
    tiny = ComparisonDiskCache(tmp_path / "tiny", max_bytes=10)
    assert not tiny.save("TL", "c" * 32, "matrix", {}, big)  # larger than the budget


def test_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    assert cc.default_cache() is None
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DISK_MB", "5")
    got = cc.default_cache()
    assert got.root == tmp_path / "x" and got.max_bytes == 5 * 2**20
    monkeypatch.delenv("VISIONHOUSING_COMPARISONS_DIR")
    assert cc.default_cache().root == ROOT / "data" / "cache" / "comparisons"


def test_api_serves_from_disk_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path / "cmp"))
    url = f"/api/v1/states/TL/comparisons?part=matrix&draws={DRAWS}&seed=4"
    first = AsgiClient(create_app(packs_dir=tmp_path)).get(url)
    assert first.status == 200
    assert list((tmp_path / "cmp" / "TL").rglob("matrix.json.gz"))

    def boom(*a, **k):
        raise AssertionError("recomputed")

    monkeypatch.setattr("visionhousing.engine.comparisons.comparisons", boom)
    second = AsgiClient(create_app(packs_dir=tmp_path)).get(url)  # a new server
    assert second.status == 200 and second.json() == first.json()


def test_warm_command_keeps_every_part(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", str(tmp_path / "cmp"))
    code = main(["comparisons", "--warm", "--state", "TL", "--part", "interactions,matrix",
                 "--draws", str(DRAWS)])  # fmt: skip
    assert code == 0
    assert "matrix: kept" in capsys.readouterr().out
    ctx = load_context("TL")
    cache = cc.default_cache()
    # the warmed parts equal what a request would compute
    run, calls = _counting()
    out = cached_comparisons(
        ctx, ["interactions", "matrix"], seed=0, draws=DRAWS, cache=cache, compute=run
    )
    assert calls == []
    fresh = json.loads(json.dumps(cc._clean(comparisons(ctx, ["interactions", "matrix"],
                                                        seed=0, draws=DRAWS))))  # fmt: skip
    assert out == fresh
    assert main(["comparisons", "--warm", "--state", "TL", "--part", "matrix",
                 "--draws", str(DRAWS)]) == 0  # fmt: skip
    assert "already on disk" in capsys.readouterr().out
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
    assert main(["comparisons", "--warm", "--state", "TL"]) == 1
