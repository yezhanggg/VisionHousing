"""The static-site exporter (``visionhousing export-static``), on Testland.

Each saved file must hold exactly what the live interface answers, the index must list
what was saved, and the folder must stay inside the static site's size limit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zlib
from pathlib import Path

import pytest

from tests.asgi_client import AsgiClient
from visionhousing.api import create_app
from visionhousing.cli import main
from visionhousing.compact_profiles import TemplateBuilder, assemble, encode_county
from visionhousing.export_static import (
    BENCHMARK_PACKAGE,
    GZIP_LIMIT_BYTES,
    SIZE_LIMIT_BYTES,
    TRACT_FILE_BUDGET_BYTES,
    ExportError,
    export_static,
    fetch_tract_profiles,
    folder_gzip_size,
    folder_size,
    over_limit,
    pack_json,
    read_saved,
    share_state_pool,
    to_columns,
    unpack,
)

API = "/api/v1"
DRAWS = 100
API_JS = Path(__file__).resolve().parent.parent / "web" / "js" / "lib" / "api.js"
TRACT_REFORMS = ["adu", "missing_middle", "tod", "pa_hb2186", "pa_sb1346"]


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    return tmp_path_factory.mktemp("packs")


@pytest.fixture(scope="module")
def exported(tmp_path_factory, packs):
    out = tmp_path_factory.mktemp("site") / "data"
    report = export_static("tl", out, packs_dir=packs, draws=DRAWS)
    return out, report


@pytest.fixture(scope="module")
def client(packs):
    return AsgiClient(create_app(packs_dir=packs))


def read(path):
    """A saved file as the live reply would read (packed files unpacked)."""
    return read_saved(path)


def county_profile(out, fips):
    """A saved county profile as the browser reads it (whole, or rebuilt from compact files)."""
    index = read(out / "TL" / "index.json")
    if index["county_profile_format"] == "compact":
        template = read(out / "TL" / "profiles" / "county-template.json")
        return assemble(template, read(out / "TL" / "profiles" / "county-values.json"), fips)
    return read(out / "TL" / "profiles" / f"{fips}.json")


def same_reply(path, body):
    """A saved file equals the live reply: byte for byte, or exactly once unpacked."""
    saved = path.read_bytes()
    if saved == body:
        return True
    packed = json.loads(saved)
    return packed.get("$packed") == 1 and read_saved(path) == json.loads(body)


def test_index_lists_what_was_saved(exported):
    out, report = exported
    index = read(out / "TL" / "index.json")
    assert index["state"] == "TL" and index["seed"] == 0 and index["draws"] == DRAWS
    assert index["package"] == list(BENCHMARK_PACKAGE)
    assert index["runs"] == ["baseline", *BENCHMARK_PACKAGE, "package"]
    for key in index["runs"]:
        assert (out / "TL" / "runs" / f"{key}.json").is_file()
        assert (out / "TL" / "compare" / f"{key}.json").is_file()
    for fips in index["counties"]:
        assert (out / "TL" / "counties" / f"{fips}.json").is_file()
        assert county_profile(out, fips)["geoid"] == fips
    assert index["county_profile_format"] in ("full", "compact")
    assert index["tract_profiles"] is True and report.tract_profiles
    assert index["explanations"] is True
    for name in ["summary", "ledger", "provenance", "validation"]:
        assert (out / "TL" / f"{name}.json").is_file()
    for name in ["states", "reforms", "ledger", "provenance"]:
        assert (out / f"{name}.json").is_file()
    assert [s["code"] for s in read(out / "states.json")["states"]] == ["TL"]


def test_files_equal_the_live_replies(exported, client):
    out, _ = exported
    pairs = {
        "TL/summary.json": f"{API}/states/TL/summary",
        "TL/validation.json": f"{API}/validation?state=TL",
        "TL/places/counties.json": f"{API}/places/TL?level=county&limit=500",
        "reforms/adu.json": f"{API}/reforms/adu",
        "TL/counties/99001.json": f"{API}/states/TL/counties/99001?r="
        + ",".join(BENCHMARK_PACKAGE)
        + f"&seed=0&draws={DRAWS}",
    }
    for rel, url in pairs.items():
        assert same_reply(out / rel, client.get(url).body), rel
    assert canon(county_profile(out, "99001")) == canon(
        client.get(f"{API}/profile/TL/99001").json()
    )
    # A reform alone is saved with its top drivers (the Reform screen shows them).
    body = {"state": "TL", "reforms": ["adu"], "seed": 0, "draws": DRAWS, "drivers": True}
    assert same_reply(out / "TL" / "runs" / "adu.json", client.post(f"{API}/run", body).body)
    assert json.loads((out / "TL" / "runs" / "adu.json").read_bytes()).get("$packed") == 1
    run = read(out / "TL" / "runs" / "package.json")
    assert run["reforms"] == list(BENCHMARK_PACKAGE) and run["explanation"] is not None
    # The screens' other default replies: maps, outlines, evidence, report card, exports.
    query = f"s=TL&r=adu&seed=0&draws={DRAWS}"
    more = {
        "TL/maps/adu.json": f"{API}/states/TL/map?{query}",
        "TL/outlines.json": f"{API}/states/TL/outlines",
        "TL/report-card.json": f"{API}/report-card?state=TL",
        "evidence.json": f"{API}/evidence",
        "TL/exports/adu.csv": f"{API}/export/csv?{query}",
    }
    for rel, url in more.items():
        assert same_reply(out / rel, client.get(url).body), rel
    # Files offered as downloads are never packed.
    for rel in ["TL/ledger.json", "TL/provenance.json", "TL/report-card.json"]:
        assert "$packed" not in read(out / rel), rel
    index = read(out / "TL" / "index.json")
    assert index["extra_runs"] == ["pa_hb2186", "pa_sb1346"]
    for key in [*index["runs"], *index["extra_runs"]]:
        assert (out / "TL" / "maps" / f"{key}.json").is_file()
        assert (out / "TL" / "exports" / f"{key}.html").is_file()


def test_tract_profiles_are_saved_per_county(exported):
    out, _ = exported
    tracts = read(out / "TL" / "places" / "tracts-99001.json")["places"]
    assert tracts and all(t["geoid"].startswith("99001") for t in tracts)
    assert all((out / "TL" / "profiles" / f"{t['geoid']}.json").is_file() for t in tracts)


def test_size_report_and_limit(exported):
    out, report = exported
    assert report.total == folder_size(out) < SIZE_LIMIT_BYTES
    assert 0 < folder_gzip_size(out) < min(GZIP_LIMIT_BYTES, folder_size(out) / 2)
    assert over_limit(out) == ""
    text = report.text()
    assert "runs" in text and "whole folder" in text and "gzip" in text


def test_budget_counts_compressed_bytes_and_has_a_ceiling(tmp_path, monkeypatch):
    """The budget is the gzip size; the uncompressed size has its own, higher ceiling."""
    import visionhousing.export_static as es

    assert es.GZIP_LIMIT_BYTES == 12 * 2**20 and es.SIZE_LIMIT_BYTES == 60 * 2**20
    (tmp_path / "a.json").write_text("[" + "1," * 50_000 + "1]", encoding="utf-8")
    raw, packed = folder_size(tmp_path), folder_gzip_size(tmp_path)
    assert packed < raw / 50
    monkeypatch.setattr(es, "GZIP_LIMIT_BYTES", packed - 1)
    assert "compressed budget" in over_limit(tmp_path)
    monkeypatch.setattr(es, "GZIP_LIMIT_BYTES", packed)
    assert over_limit(tmp_path) == ""
    monkeypatch.setattr(es, "SIZE_LIMIT_BYTES", raw - 1)
    assert "ceiling" in over_limit(tmp_path)


def test_county_profiles_compact_equal_the_live_replies(exported, client):
    """Compact county profiles (smaller compressed for Testland) rebuild to the live replies."""
    out, report = exported
    index = read(out / "TL" / "index.json")
    assert index["county_profile_format"] == "compact"
    assert not list((out / "TL" / "profiles").glob("?????.json"))
    data = read(out / "TL" / "profiles" / "county-values.json")
    assert data["geoids"] == index["counties"] and data["raw"] == {}
    for fips in index["counties"]:
        live = client.get(f"{API}/profile/TL/{fips}").json()
        assert canon(county_profile(out, fips)) == canon(live), fips
    assert any("county profiles saved compact" in n for n in report.notes)


def test_tract_fetch_resumes_from_its_cache(packs, tmp_path):
    """Each county's replies are kept as they arrive; a second run reads them back unchanged."""
    from visionhousing.export_static import _Caller

    app = create_app(packs_dir=packs, web_dir=tmp_path / ".no-web", workers=1)
    call = _Caller(app)
    asked = []

    def fetch(path):
        asked.append(path)
        status, body = call("GET", path)
        assert status == 200
        return body

    lines = []
    first = fetch_tract_profiles(fetch, "TL", ["99001", "99003"], cache=tmp_path / "c",
                                 progress=lines.append)  # fmt: skip
    assert len(lines) == 2 and "99001 (1 of 2)" in lines[0] and "asked" in lines[0]
    assert sorted(p.name for p in (tmp_path / "c").iterdir()) == ["99001.json.gz", "99003.json.gz"]
    # Stopped after the first county: only the second one is asked for again.
    (tmp_path / "c" / "99003.json.gz").unlink()
    asked.clear()
    lines.clear()
    again = fetch_tract_profiles(fetch, "TL", ["99001", "99003"], cache=tmp_path / "c",
                                 progress=lines.append)  # fmt: skip
    assert "from cache" in lines[0] and "asked" in lines[1]
    assert all("99003" in p for p in asked) and asked
    for fips in first:
        assert first[fips][0] == again[fips][0]
        assert [(g, zlib.decompress(b)) for g, b in first[fips][1]] == [
            (g, zlib.decompress(b)) for g, b in again[fips][1]
        ]


def test_tract_cache_key_follows_code_assumptions_and_sections(tmp_path, monkeypatch):
    """Editing a profile section, a law or feature file, or an assumption file gives a new key."""
    from visionhousing.engine import hashes
    from visionhousing.export_static import _cache_key

    pkg = tmp_path / "pkg"
    for rel in ("profile/sections/homes.py", "law/timeline.py", "features/acs.py",
                "data/sources.yaml", "engine/run.py"):  # fmt: skip
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text(f"# {rel}\n", encoding="utf-8")
    assume = tmp_path / "assumptions"
    (assume / "law").mkdir(parents=True)
    (assume / "law" / "priorities.yaml").write_text("a: 1\n", encoding="utf-8")

    # The run fingerprint without its per-process cache (which holds one folder only).
    monkeypatch.setattr(hashes, "code_hash", lambda folder=None: hashes._code_hash.__wrapped__(
        str(folder)))  # fmt: skip

    def key(pack="p1"):
        return _cache_key({"pack_hash": pack}, package=pkg, assumptions=assume)

    keys = {key()}
    assert key() in keys  # the same inputs give the same key
    assert key("p2") not in keys
    for path, text in [
        (pkg / "profile" / "sections" / "homes.py", "# edited\n"),
        (pkg / "law" / "timeline.py", "# edited\n"),
        (pkg / "features" / "acs.py", "# edited\n"),
        (pkg / "engine" / "run.py", "# edited\n"),  # only in the code fingerprint
        (assume / "law" / "priorities.yaml", "a: 2\n"),
        (pkg / "data" / "sources.yaml", "edited: 1\n"),
    ]:
        path.write_text(text, encoding="utf-8")
        new = key()
        assert new not in keys, path
        keys.add(new)


def test_tract_lists_are_one_county_and_never_cut(packs, tmp_path):
    """Each county's tract list holds only its own tracts; a cut list stops the export."""
    from visionhousing.export_static import _Caller

    call = _Caller(create_app(packs_dir=packs, web_dir=tmp_path / ".no-web", workers=1))

    def fetch(path):
        status, body = call("GET", path)
        assert status == 200, body
        return body

    got = fetch_tract_profiles(fetch, "TL", ["99001", "99003"])
    for fips, (listing, items) in got.items():
        listed = json.loads(listing)
        assert listed["total"] == len(listed["places"]) == len(items) > 0
        assert all(p["geoid"][:5] == fips for p in listed["places"])

    def cut(path):
        body = json.loads(fetch(path))
        if "/places/" in path:
            body["places"] = body["places"][:1]
        return json.dumps(body).encode()

    with pytest.raises(ExportError, match="cut"):
        fetch_tract_profiles(cut, "TL", ["99001"])

    def foreign(path):
        body = json.loads(fetch(path))
        if "/places/" in path:
            body["places"][0] = {**body["places"][0], "geoid": "99003000100"}
        return json.dumps(body).encode()

    with pytest.raises(ExportError, match="not in it"):
        fetch_tract_profiles(foreign, "TL", ["99001"])


@pytest.fixture(scope="module")
def compact(tmp_path_factory, packs):
    """Testland exported with a tiny tract budget: tract profiles over it are saved compact."""
    out = tmp_path_factory.mktemp("compact") / "data"
    report = export_static(
        "TL", out, packs_dir=packs, draws=DRAWS, tract_budget_mb=0.01, explain="no"
    )
    return out, report


def test_tract_budget_and_no_explanations(compact):
    out, report = compact
    index = read(out / "TL" / "index.json")
    assert index["tract_profiles"] is True and index["explanations"] is False
    assert index["tract_profile_format"] == "compact"
    assert not list((out / "TL" / "profiles").glob("???????????.json"))
    assert (out / "TL" / "profiles" / "tract-template.json").is_file()
    assert read(out / "TL" / "runs" / "adu.json")["explanation"] is None
    assert any("budget" in n for n in report.notes)


def test_no_tract_profiles(tmp_path, packs):
    out = tmp_path / "data"
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")
    index = read(out / "TL" / "index.json")
    assert index["tract_profiles"] is False and index["tract_profile_format"] == ""
    assert not list((out / "TL" / "profiles").glob("tract*.json"))
    assert not list((out / "TL" / "places").glob("tracts-*.json"))


def canon(value):
    """Exact comparison text; key order counts (the screen numbers notes by key order)."""
    return json.dumps(value, ensure_ascii=False)


def js_canon(value):
    """``canon`` as a browser reads JSON: 92500.0 and 92500 are the same number there."""

    def norm(v):
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v

    return canon(norm(value))


def test_compact_tract_profiles_equal_the_live_replies(compact, client):
    """Every tract rebuilt from the template and its county file equals the live reply."""
    out, _ = compact
    index = read(out / "TL" / "index.json")
    template = read(out / "TL" / "profiles" / "tract-template.json")
    count = 0
    for fips in index["counties"]:
        data = read(out / "TL" / "profiles" / f"tracts-{fips}.json")
        assert data["$tracts"] == 1 and data["raw"] == {}
        listed = [p["geoid"] for p in read(out / "TL" / "places" / f"tracts-{fips}.json")["places"]]
        assert data["geoids"] == [g for g in listed if g.startswith(fips)]
        for geoid in data["geoids"]:
            live = client.get(f"{API}/profile/TL/{geoid}").json()
            assert canon(assemble(template, data, geoid)) == canon(live), geoid
            count += 1
    assert count > 30
    # Much smaller than the whole profiles.
    whole = sum(len(client.get(f"{API}/profile/TL/{g}").body) for g in data["geoids"])
    assert (out / "TL" / "profiles" / f"tracts-{fips}.json").stat().st_size < whole / 5


COMPACT_JS_CHECK = """
import { readFileSync } from "node:fs";
const [apiPath, dataDir, edgePath, ...geoids] = process.argv.slice(1);
const env = { VITE_DATA_MODE: "static", BASE_URL: "/" };
const src = readFileSync(apiPath, "utf8").replace("import.meta.env", JSON.stringify(env));
globalThis.fetch = async (url) => {
  try {
    return new Response(readFileSync(`${dataDir}/${String(url).replace(/^\\/data\\//, "")}`));
  } catch {
    return new Response("{}", { status: 404 });
  }
};
const mod = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
const { api } = mod;
const edge = JSON.parse(readFileSync(edgePath, "utf8"));
const out = { edge: edge.geoids.map((g) => mod.assembleTract(edge.template, edge.data, g)) };
for (const g of geoids) {
  try {
    out[g] = await api.profile("tl", g);
  } catch (e) {
    out[g] = { error: e.name, message: e.message };
  }
}
console.log(JSON.stringify(out));
"""


def test_api_js_rebuilds_compact_tract_profiles(compact, client, tmp_path):
    """The static client rebuilds each compact tract profile exactly as the live reply."""
    out, _ = compact
    profiles, builder, data = edge_case()
    edge = tmp_path / "edge.json"
    edge.write_text(json.dumps({"template": builder.template(), "data": data,
                                "geoids": data["geoids"]}), encoding="utf-8")  # fmt: skip
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found: install Node 22.12+")
    geoids = [
        p["geoid"]
        for fips in read(out / "TL" / "index.json")["counties"]
        for p in read(out / "TL" / "places" / f"tracts-{fips}.json")["places"]
    ]
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, "--input-type=module", "-e", COMPACT_JS_CHECK, str(API_JS), str(out), str(edge),
         *geoids, "99001", "99001999999"],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    got = json.loads(done.stdout)
    for geoid in geoids:
        live = client.get(f"{API}/profile/TL/{geoid}").json()
        assert js_canon(got[geoid]) == js_canon(live), geoid
    # Counties come back from their own compact files.
    assert js_canon(got["99001"]) == js_canon(client.get(f"{API}/profile/TL/99001").json())
    assert got["99001999999"]["error"] == "LocalOnlyError"
    assert [js_canon(p) for p in got["edge"]] == [js_canon(p) for p in profiles]


def edge_case():
    """Three small profiles with every hard case; returns them, the builder and the county file."""
    profiles = [
        {"a": 1, "b": {"x": "4,286 people", "z": 1},
         "c": [{"id": "p", "v": 1.5}, {"id": "q", "v": 2}], "d": None, "s": ["t", "u"],
         "w": "same", "f": 1.0},
        {"a": 2, "c": [{"id": "q", "v": True}], "d": {"y": [1, 2]}, "s": ["t"], "w": "same",
         "b": {"z": 2, "x": "12 people and 3.5 homes"}, "f": 1},
        {"a": 3, "b": {"x": ""}, "c": [{"id": "q", "v": None}, {"id": "p", "v": "1"}],
         "d": [], "s": [], "w": "same", "f": True},
    ]  # fmt: skip
    builder = TemplateBuilder()
    for p in profiles:
        builder.add(p)
    return profiles, builder, encode_county(builder, ["1", "2", "3"], profiles)


def test_compact_template_edge_cases():
    """Kinds that change, keys and items that come and go, wordings and odd orders."""
    profiles, builder, data = edge_case()
    template = builder.template()
    for geoid, p in zip(data["geoids"], profiles, strict=True):
        assert canon(assemble(template, data, geoid)) == canon(p)
        # Key order is kept, at the top and inside (canon compares it too).
        got = assemble(template, data, geoid)
        assert list(got) == list(p) and list(got["b"]) == list(p["b"])
    # The second profile puts its keys in another order: both objects carry an order column.
    assert len(template) == 3 and len(dict((k, n) for k, n, _p in template[1])["b"]) == 3
    # The third lists p and q the other way round from the first: the list keeps an order.
    assert data["raw"] == {}
    tags = {key: node[0] for key, node, _p in template[1]}
    assert tags == {"a": "c", "b": "d", "c": "o", "d": "x", "s": "l", "w": "k", "f": "c"}
    # A profile the template cannot hold (a new key) is saved whole.
    odd = encode_county(builder, ["9"], [{**profiles[0], "new": 1}])
    assert list(odd["raw"]) == ["9"] and assemble(template, odd, "9")["new"] == 1


def test_rerun_replaces_the_state_and_refuses_foreign_folders(tmp_path, packs):
    out = tmp_path / "data"
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")
    stray = out / "TL" / "runs" / "old.json"
    stray.write_text("{}", encoding="utf-8")
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")
    assert not stray.exists()
    (out / "TL" / "index.json").unlink()
    with pytest.raises(ExportError, match="not an earlier export"):
        export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no")


def test_unknown_state_writes_nothing(tmp_path, packs):
    out = tmp_path / "data"
    with pytest.raises(ExportError, match="404"):
        export_static("OH", out, packs_dir=packs, draws=DRAWS)
    assert not out.exists()


def test_cli(tmp_path, capsys):
    out = tmp_path / "data"
    code = main(["export-static", "--state", "TL", "--out", str(out), "--draws", str(DRAWS),
                 "--tracts", "no", "--explain", "no", "--comparisons", "no"])  # fmt: skip
    assert code == 0
    assert "export-static TL" in capsys.readouterr().out
    assert (out / "TL" / "index.json").is_file()
    assert read(out / "TL" / "index.json")["comparisons"] is False


def test_pack_round_trip_is_exact():
    """Packing pools repeated blocks and unpacking gives back exactly the same value."""
    block = {"id": "fees.fee_total_per_unit", "label": "Development fees per unit"}
    text = "a long repeated sentence that is worth pooling"
    value = {
        "numbers": [{"a": block, "b": [block, text], "x": 1.234567890123}, {"a": block, "t": text}],
        "short": ["ab", "ab"],
        "nested": [[block, [block]], [block, [block]]],
    }
    packed = pack_json(value)
    assert packed["$packed"] == 1 and packed["$pool"]
    assert len(json.dumps(packed)) < len(json.dumps(value))
    assert unpack(packed) == value
    assert pack_json({"no": "repeats"}) == {"no": "repeats"}
    assert unpack({"plain": 1}) == {"plain": 1}


def test_columns_round_trip_is_exact():
    """Lists of like objects become columns; unpacking gives back the same lists."""
    rows = [{"geoid": "1", "p": 1.5, "t": {"a": [1]}}, {"geoid": "2", "p": 2, "t": {"a": []}}]
    value = {"rows": rows, "mixed": [{"a": 1}, {"b": 2}], "one": [{"a": 1}], "deep": [rows, rows]}
    cols = to_columns(value)
    inner = {"$c": ["a"], "$v": [[[1], []]]}
    assert cols["rows"] == {"$c": ["geoid", "p", "t"], "$v": [["1", "2"], [1.5, 2], inner]}
    assert cols["mixed"] == value["mixed"] and cols["one"] == value["one"]
    packed = {"$packed": 1, "$pool": [], "$data": cols}
    assert unpack(packed) == value
    assert unpack(pack_json(cols)) == value  # pooled key lists and columns come back too


def shared_folder(tmp_path):
    """A small state folder whose screen files repeat each other."""
    state = tmp_path / "XX"
    county = {"fips": "99001", "name": "First County", "formula": "sum of the tract P50s"}
    rows = [{"geoid": f"9900100{i}", "p50": i * 1.5, "shown": f"{i} homes"} for i in range(5)]
    files = {
        "runs/adu.json": {"counties": [county, {**county, "fips": "99003"}], "tracts": rows},
        "runs/package.json": {"counties": [county], "tracts": rows, "extra": "only here"},
        "districts/lower/adu.json": {"rows": rows, "note": "a district note"},
        "maps/adu.json": {"alone": "nothing here is in another file at all, really"},
    }
    for rel, value in files.items():
        path = state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    return state, files


def test_shared_pool_round_trip_is_exact(tmp_path):
    """Blocks that two files repeat go to ST/pool.json; every file reads back exactly."""
    state, files = shared_folder(tmp_path)
    changed = share_state_pool(state)
    assert (state / "pool.json").is_file() and "pool.json" in changed
    assert json.loads((state / "runs" / "adu.json").read_text())["$shared"] == 1
    assert "maps/adu.json" not in changed  # shares nothing: left as it was
    for rel, value in files.items():
        assert read_saved(state / rel) == value, rel
    with pytest.raises(ValueError, match="shared pool"):
        unpack(json.loads((state / "runs" / "adu.json").read_text()))
    # A second pass reads the old pool first and gives the same files back.
    share_state_pool(state)
    for rel, value in files.items():
        assert read_saved(state / rel) == value, rel
    # A file that no longer shares anything is saved on its own again.
    (state / "runs" / "package.json").unlink()
    (state / "districts" / "lower" / "adu.json").unlink()
    share_state_pool(state)
    assert read_saved(state / "runs" / "adu.json") == files["runs/adu.json"]


def test_shared_pool_refuses_a_reply_that_looks_like_a_mark(tmp_path):
    state, _ = shared_folder(tmp_path)
    (state / "runs" / "odd.json").write_text(json.dumps({"x": {"$s": 1}}), encoding="utf-8")
    with pytest.raises(ExportError, match="mark"):
        share_state_pool(state)


SHARED_JS_CHECK = """
import { readFileSync } from "node:fs";
const [apiPath, dataDir, ...paths] = process.argv.slice(1);
const env = { VITE_DATA_MODE: "static", BASE_URL: "/" };
const src = readFileSync(apiPath, "utf8").replace("import.meta.env", JSON.stringify(env));
const mod = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
const read = (p) => JSON.parse(readFileSync(`${dataDir}/${p}`, "utf8"));
const out = {};
for (const p of paths) {
  const value = read(p);
  out[p] = value.$shared ? mod.unpack(value, mod.makePool(read(`${p.split("/")[0]}/pool.json`)))
                         : mod.unpack(value);
}
console.log(JSON.stringify(out));
"""


def test_exported_state_shares_one_pool_and_api_js_reads_it(exported):
    """The export writes ST/pool.json; the static client reads every shared file exactly."""
    out, report = exported
    assert (out / "TL" / "pool.json").is_file() and "shared pool" in report.sizes
    saved = {p.relative_to(out).as_posix(): json.loads(p.read_bytes())
             for p in (out / "TL").rglob("*.json")}  # fmt: skip
    shared = sorted(rel for rel, v in saved.items() if isinstance(v, dict) and v.get("$shared"))
    assert any(p.startswith("TL/runs/") for p in shared)
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found: install Node 22.12+")
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, "--input-type=module", "-e", SHARED_JS_CHECK, str(API_JS), str(out), *shared],
        capture_output=True, text=True, check=True, timeout=120,
    )  # fmt: skip
    got = json.loads(done.stdout)
    for rel in shared:  # keys sorted: a browser puts number-like keys ("2", "1") first anyway
        js, py = (json.loads(js_canon(v)) for v in (got[rel], read(out / rel)))
        assert json.dumps(js, sort_keys=True) == json.dumps(py, sort_keys=True), rel


def test_tract_columns_are_saved_for_every_run(exported, client):
    out, report = exported
    index = read(out / "TL" / "index.json")
    every = [*index["runs"], *index["extra_runs"]]
    assert index["tract_points"] is True and index["tracts"] == every
    assert index["tract_funnels"] == TRACT_REFORMS  # one tract-level reform each
    query = f"seed=0&draws={DRAWS}"
    pairs = {"TL/tract-points.json": f"{API}/states/TL/tract-points"}
    for key in every:
        reforms = ",".join(
            BENCHMARK_PACKAGE if key == "package" else [] if key == "baseline" else [key]
        )
        pairs[f"TL/tracts/{key}.json"] = f"{API}/states/TL/tracts?s=TL&r={reforms}&{query}"
    for key in TRACT_REFORMS:
        pairs[f"TL/tract-funnel/{key}.json"] = f"{API}/states/TL/tract-funnel?s=TL&r={key}&{query}"
    for rel, url in pairs.items():
        assert same_reply(out / rel, client.get(url).body), rel
    saved = {p.stem for p in (out / "TL" / "tract-funnel").glob("*.json")}
    assert saved == set(TRACT_REFORMS)
    for path in [out / "TL" / "tract-points.json", *(out / "TL").glob("tract*/*.json")]:
        assert path.stat().st_size <= TRACT_FILE_BUDGET_BYTES, path.name
    assert not any("tract-column files over" in n for n in report.notes)
    package = read(out / "TL" / "tracts" / "package.json")
    points = read(out / "TL" / "tract-points.json")
    assert package["geoids"] == points["geoids"]
    assert set(package["county_only"]) == {"parking", "fees"}


API_JS_CHECK = """
import { readFileSync } from "node:fs";
const [apiPath, mode, dataDir] = process.argv.slice(1);
const env = mode === "static" ? { VITE_DATA_MODE: "static", BASE_URL: "/" } : { BASE_URL: "/" };
const src = readFileSync(apiPath, "utf8").replace("import.meta.env", JSON.stringify(env));
const asked = [];
globalThis.fetch = async (url) => {
  asked.push(String(url));
  if (mode !== "static") return new Response(JSON.stringify({ url: String(url) }));
  try {
    return new Response(readFileSync(`${dataDir}/${String(url).replace(/^\\/data\\//, "")}`));
  } catch {
    return new Response("{}", { status: 404 });
  }
};
const mod = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
const { api } = mod;
const out = {};
const ask = async (name, f) => {
  try { out[name] = await f(); } catch (e) { out[name] = { error: e.name, message: e.message }; }
};
if (mode === "static") {
  await ask("points", () => api.tractPoints("tl"));
  await ask("adu", () => api.stateTracts("TL", ["adu"]));
  await ask("baseline", () => api.stateTracts("TL", []));
  const shuffled = ["fees", "parking", "tod", "missing_middle", "adu"];
  await ask("package", () => api.stateTracts("TL", shuffled));
  await ask("mix", () => api.stateTracts("TL", ["adu", "fees"]));
  await ask("custom", () => api.stateTracts("TL", ["adu"], { "adu.takeup_feasible_annual": "h" }));
  await ask("funnel", () => api.tractFunnel("TL", "adu"));
  await ask("noFunnel", () => api.tractFunnel("TL", "fees"));
  await ask("readouts", async () => {
    const run = await api.run({ state: "TL", reforms: ["fees"], seed: 0 });
    const county = await api.county("TL", "99001");
    return {
      readouts: mod.readouts(run, "fees"),
      revenue: mod.readoutCounties(run, "fees", "revenue_given_up"),
      texts: mod.readoutTexts(run, "fees"),
      sentences: mod.readoutSentences(run, "fees"),
      narrowing: mod.narrowing(run, "fees"),
      frequency: mod.frequencyText(run),
      county: mod.countyReadouts(county, "fees"),
      none: mod.readouts(run, "adu"),
    };
  });
} else {
  await ask("tracts", () => api.stateTracts("TL", ["adu", "fees"], { "adu.x": "h" }));
  await ask("one", () => api.stateTracts("tl", "adu"));
  await ask("funnel", () => api.tractFunnel("TL", "tod"));
  await ask("points", () => api.tractPoints("TL"));
}
console.log(JSON.stringify(out));
"""


def api_js(mode: str, data: Path) -> dict:
    """Run web/js/lib/api.js under Node in ``mode``, reading exported files for static mode."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found: install Node 22.12+")
    done = subprocess.run(  # noqa: S603 (fixed command, no shell)
        [node, "--input-type=module", "-e", API_JS_CHECK, str(API_JS), mode, str(data)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return json.loads(done.stdout)


def test_api_js_reads_the_saved_tract_columns(exported, client):
    """The static client returns exactly the live replies; anything unsaved needs the app."""
    out, _ = exported
    got = api_js("static", out)
    query = f"seed=0&draws={DRAWS}"
    live = {
        "points": f"{API}/states/TL/tract-points",
        "adu": f"{API}/states/TL/tracts?r=adu&{query}",
        "baseline": f"{API}/states/TL/tracts?r=&{query}",
        "package": f"{API}/states/TL/tracts?r={','.join(BENCHMARK_PACKAGE)}&{query}",
        "funnel": f"{API}/states/TL/tract-funnel?r=adu&{query}",
    }
    for name, url in live.items():
        assert got[name] == client.get(url).json(), name
    for name in ("mix", "custom", "noFunnel"):
        assert got[name]["error"] == "LocalOnlyError", name
    assert "mix of reforms" in got["mix"]["message"]
    assert "assumptions" in got["custom"]["message"]
    asked = api_js("live", out)
    assert asked["tracts"]["url"] == "/api/v1/states/TL/tracts?s=TL&r=adu,fees&a=adu.x~h"
    assert asked["one"]["url"] == "/api/v1/states/TL/tracts?s=TL&r=adu"
    assert asked["funnel"]["url"] == "/api/v1/states/TL/tract-funnel?s=TL&r=tod"
    assert asked["points"]["url"] == "/api/v1/states/TL/tract-points"


def test_api_js_reads_the_saved_readouts(exported, client):
    """The readout accessors find the engine's records in the saved run and county files."""
    out, _ = exported
    got = api_js("static", out)["readouts"]
    assert "error" not in got, got
    live = client.post(
        f"{API}/run",
        {"state": "TL", "reforms": ["fees"], "seed": 0, "draws": DRAWS, "explain": True,
         "drivers": True},
    ).json()  # fmt: skip
    fees = live["reform_results"]["fees"]
    assert got["readouts"] == fees["readouts"]
    assert got["readouts"]["cost_per_added_home"]["formula"]
    assert got["revenue"] == fees["readout_counties"]["revenue_given_up"]
    assert got["texts"] == fees["readout_texts"]
    assert got["sentences"] == live["explanation"]["readouts"]["fees"]
    assert got["narrowing"] == live["explanation"]["narrowing"]["fees"]
    assert got["frequency"]["text"].startswith("In eight of ten simulated futures")
    assert (
        got["county"]["revenue_given_up"] == fees["readout_counties"]["revenue_given_up"]["99001"]
    )
    assert got["none"] == {}


def test_saved_profiles_carry_charts_summaries_and_ranks(exported):
    out, _ = exported
    county = county_profile(out, "99001")
    people = next(s for s in county["sections"] if s["key"] == "people")
    assert people["headline"]["fact"] == "people.population"
    assert people["summary_points"] and people["charts"]
    chart = people["charts"][0]
    assert {"id", "kind", "items", "total", "compare", "source_ids"} <= set(chart)
    assert all(i["share"] is not None for i in chart["items"])
    assert county["ranks"] and county["ranks"][0]["text_rank"].endswith("of 4")
    tract = read(out / "TL" / "profiles" / "99001000100.json")
    assert tract["ranks"] == [] and any(s["charts"] for s in tract["sections"])


def test_permit_years_are_saved(exported, client):
    """The year slider's file equals the live reply, and the index says it is there."""
    out, _report = exported
    assert read(out / "TL" / "index.json")["permit_years"] is True
    reply = client.get(f"{API}/states/TL/permit-years").body
    assert same_reply(out / "TL" / "permit-years.json", reply)


def test_county_profiles_stay_whole_when_compact_does_not_save(tmp_path, packs, client):
    """With one county there is nothing to share: the profile is saved whole, as the live reply."""
    from visionhousing.export_static import ExportReport, _save_county_profiles, _Writer

    body = client.get(f"{API}/profile/TL/99001").body
    w = _Writer(None, tmp_path, ExportReport("TL", tmp_path))
    assert _save_county_profiles(w, "TL", [("99001", body)]) == "full"
    assert same_reply(tmp_path / "TL" / "profiles" / "99001.json", body)
    assert not (tmp_path / "TL" / "profiles" / "county-values.json").exists()


def test_comparisons_cards_grades_and_districts_are_saved(exported, client):
    """Each newer screen file is the live reply, and its index flag says it is there."""
    from visionhousing.engine.comparisons import PARTS

    out, _ = exported
    index = read(out / "TL" / "index.json")
    for flag in ["comparisons", "confidence", "plausibility", "lever", "implementation", "grades"]:
        assert index[flag] is True, flag
    assert index["districts"] is True and index["district_chambers"] == ["lower", "upper"]
    sampling = f"seed=0&draws={DRAWS}"
    pairs = {f"TL/comparisons/{p}.json": f"{API}/states/TL/comparisons?part={p}&{sampling}"
             for p in PARTS}  # fmt: skip
    pairs.update(
        {
            "TL/confidence.json": f"{API}/confidence/TL",
            "TL/plausibility.json": f"{API}/plausibility/TL?{sampling}",
            "TL/implementation.json": f"{API}/implementation/TL",
            "TL/grades/baseline.json": f"{API}/grades/TL?{sampling}",
            "TL/grades/package.json": f"{API}/grades/TL?r={','.join(BENCHMARK_PACKAGE)}&{sampling}",
            "TL/districts/outlines-upper.json": f"{API}/states/TL/district-outlines?chamber=upper",
            "TL/districts/lower/adu.json": f"{API}/states/TL/districts?chamber=lower&r=adu&"
            + sampling,
        }
    )
    for key in BENCHMARK_PACKAGE:
        pairs[f"TL/lever/{key}.json"] = f"{API}/lever/TL?r={key}&{sampling}"
        pairs[f"TL/grades/{key}.json"] = f"{API}/grades/TL?r={key}&{sampling}"
    for rel, url in pairs.items():
        assert same_reply(out / rel, client.get(url).body), rel
    # A district file for every saved run, in both chambers.
    for chamber in ["lower", "upper"]:
        for key in [*index["runs"], *index["extra_runs"]]:
            assert (out / "TL" / "districts" / chamber / f"{key}.json").is_file(), (chamber, key)
    # The extra scenarios are left to the local app unless asked for.
    assert not set(index["extra_runs"]) - {"pa_hb2186", "pa_sb1346"}


def test_extras_and_no_comparisons(tmp_path, packs):
    """--extras saves each extra scenario alone; --comparisons no leaves the parts out."""
    out = tmp_path / "data"
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no",
                  comparisons=False, extras=True)  # fmt: skip
    index = read(out / "TL" / "index.json")
    extras = [r["key"] for r in read(out / "reforms.json")["reforms"] if r.get("kind") == "extra"]
    assert extras and set(extras) <= set(index["extra_runs"])
    for key in extras:
        assert (out / "TL" / "runs" / f"{key}.json").is_file()
        assert (out / "TL" / "maps" / f"{key}.json").is_file()
    assert index["comparisons"] is False and not (out / "TL" / "comparisons").exists()


def test_law_changes_and_budget_grid_hooks(tmp_path, packs, client):
    """The law-change files and the budget grid are saved and flagged only when on disk.

    Each law-change file holds the reform's payload; the grid comes from the disk cache only.
    """
    out = tmp_path / "data"
    report = export_static(
        "TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no",
        comparisons=False, law_changes=True, budget_grid=True,
    )  # fmt: skip
    index = read(out / "TL" / "index.json")
    keys = index["law_changes"]
    assert keys, report.notes
    for key in keys:
        saved = json.loads((out / "TL" / "law_changes" / f"{key}.json").read_text())
        live = client.get(f"{API}/states/TL/law-changes?r={key}&seed=0&draws={DRAWS}").json()
        assert set(saved) <= {"changes", "read_next"}
        assert saved["changes"]["reform"] == live["changes"]["reform"] == key
        assert saved["changes"]["state"] == "TL"
    grid = out / "TL" / "adu-budget-grid.json"
    assert index["adu_budget_grid"] is grid.is_file()
    if not grid.is_file():
        assert any("budget grid" in n for n in report.notes)


def test_alternatives_hook_saves_the_cached_table_only(tmp_path, packs, monkeypatch):
    """The Alternatives table is saved (compact) and flagged only when it is on disk."""
    from visionhousing.api import alternatives as alt

    body = {"state": "TL", "reforms": {"adu": {"rows": [{"diff": {"p10": 1, "p50": 2}}]}}}
    monkeypatch.setattr(alt, "payload", lambda ctx, **kw: body)
    monkeypatch.setattr(alt, "reform_view", lambda body, key: None)  # no brief appendix
    out = tmp_path / "data"
    export_static("TL", out, packs_dir=packs, draws=DRAWS, tracts="no", explain="no",
                  comparisons=False, alternatives=True)  # fmt: skip
    index = read(out / "TL" / "index.json")
    assert index["alternatives"] is True
    saved = json.loads((out / "TL" / "alternatives.json").read_text())
    assert saved == alt.compact(body) and "p10" not in json.dumps(saved)

    monkeypatch.setattr(alt, "payload", lambda ctx, **kw: None)
    out2 = tmp_path / "data2"
    report = export_static("TL", out2, packs_dir=packs, draws=DRAWS, tracts="no", explain="no",
                           comparisons=False, alternatives=True)  # fmt: skip
    assert read(out2 / "TL" / "index.json")["alternatives"] is False
    assert not (out2 / "TL" / "alternatives.json").exists()
    assert any("alternatives not cached" in n for n in report.notes)
