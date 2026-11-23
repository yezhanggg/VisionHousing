"""Network layer tests. A fake opener stands in for the network; nothing really sleeps."""

from __future__ import annotations

import email.message
import io
import random
import struct
import urllib.error
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visionhousing.data import http
from visionhousing.data.http import (
    BadRequestError,
    DiskSpaceError,
    FetchError,
    HttpClient,
    IncompleteDownloadError,
    RangeNotSupportedError,
    RetriesExhaustedError,
)

URL = "https://example.test/data/file.txt"
FAKE_KEY = "fakekey0123456789"
PLENTY = 500 * 10**9


def headers(**values: str) -> email.message.Message:
    msg = email.message.Message()
    for name, value in values.items():
        msg[name.replace("_", "-")] = value
    return msg


class FakeResponse:
    """Behaves like a urllib response: status, headers, read(n), close."""

    def __init__(self, status=200, body=b"", hdrs=None, length=None, fail_after=None):
        self.status = status
        self._buf = io.BytesIO(body)
        self.headers = headers(**(hdrs or {}))
        if length is None and status not in (204,):
            length = len(body)
        if length is not None and "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(length)
        self.fail_after = fail_after
        self.closed = False

    def read(self, n=-1):
        if self.fail_after is not None and self._buf.tell() >= self.fail_after:
            raise TimeoutError("read timed out")
        if self.fail_after is not None:
            n = min(n if n >= 0 else 10**12, self.fail_after - self._buf.tell())
        return self._buf.read(n)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(status, body=b"", hdrs=None):
    return urllib.error.HTTPError(URL, status, "error", headers(**(hdrs or {})), io.BytesIO(body))


class FakeOpener:
    """Returns (or raises) the scripted items in order and records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.timeouts = []

    def open(self, req, data=None, timeout=None):
        self.requests.append(req)
        self.timeouts.append(timeout)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def client(opener, tmp_path=None, **kw):
    waits = []
    c = HttpClient(
        opener=opener,
        sleep=waits.append,
        free_bytes=lambda _p: PLENTY,
        cache_dir=(tmp_path / "cache") if tmp_path else None,
        **kw,
    )
    return c, waits


# --------------------------------------------------------------------------- status codes


def test_200_returns_body_sends_user_agent_and_timeout():
    opener = FakeOpener(FakeResponse(200, b"hello"))
    c, waits = client(opener, timeout=12)
    assert c.get_bytes(URL, {"a": "1"}) == b"hello"
    req = opener.requests[0]
    assert req.full_url == URL + "?a=1"
    assert req.get_header("User-agent") == "visionhousing (open-source research tool)"
    assert opener.timeouts == [12]
    assert waits == []


def test_204_means_empty(tmp_path):
    c, _ = client(FakeOpener(FakeResponse(204), FakeResponse(204)))
    assert c.get_bytes(URL) == b""
    result = c.download(URL, tmp_path / "empty.txt")
    assert result.size == 0 and (tmp_path / "empty.txt").read_bytes() == b""


def test_400_fails_at_once_with_body_and_no_retry(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_KEY)
    body = f"error: unknown variable 'B99999_001E' (key {FAKE_KEY})".encode()
    opener = FakeOpener(http_error(400, body), FakeResponse(200, b"never"))
    c, waits = client(opener)
    with pytest.raises(BadRequestError) as info:
        c.get_bytes(URL, {"get": "B99999_001E", "key": FAKE_KEY})
    message = str(info.value)
    assert "unknown variable 'B99999_001E'" in message
    assert FAKE_KEY not in message and FAKE_KEY not in info.value.body
    assert "key=REDACTED" in message
    assert len(opener.requests) == 1 and waits == []


def test_429_honours_retry_after_then_succeeds():
    opener = FakeOpener(
        http_error(429, b"slow down", {"Retry_After": "7"}), FakeResponse(200, b"ok")
    )
    c, waits = client(opener)
    assert c.get_bytes(URL) == b"ok"
    assert waits == [7.0]


def test_retry_after_is_capped():
    opener = FakeOpener(http_error(429, hdrs={"Retry_After": "99999"}), FakeResponse(200, b"ok"))
    c, waits = client(opener, max_wait=30)
    c.get_bytes(URL)
    assert waits == [30]


def test_500s_back_off_exponentially_then_give_up(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_KEY)
    opener = FakeOpener(*(http_error(500, b"boom") for _ in range(3)))
    c, waits = client(opener, retries=2, backoff=1.0)
    with pytest.raises(RetriesExhaustedError) as info:
        c.get_bytes(URL, {"key": FAKE_KEY})
    assert waits == [1.0, 2.0]
    assert len(opener.requests) == 3
    assert FAKE_KEY not in str(info.value)


def test_500_then_success():
    opener = FakeOpener(http_error(503), FakeResponse(500, b"down"), FakeResponse(200, b"ok"))
    c, waits = client(opener)
    assert c.get_bytes(URL) == b"ok"
    assert waits == [1.0, 2.0]


def test_other_4xx_fails_without_retry():
    opener = FakeOpener(http_error(404, b"not here"))
    c, waits = client(opener)
    with pytest.raises(FetchError, match=r"HTTP 404.*not here"):
        c.get_bytes(URL)
    assert waits == []


def test_timeout_is_retried_then_reported():
    opener = FakeOpener(TimeoutError("timed out"), FakeResponse(200, b"ok"))
    c, waits = client(opener)
    assert c.get_bytes(URL) == b"ok" and waits == [1.0]

    opener = FakeOpener(*(TimeoutError("timed out") for _ in range(3)))
    c, waits = client(opener, retries=2)
    with pytest.raises(RetriesExhaustedError, match="TimeoutError"):
        c.get_bytes(URL)
    assert len(waits) == 2


def test_retry_after_http_date():
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    assert http.parse_retry_after("Sun, 22 Nov 2026 12:00:30 GMT", now=now) == 30
    assert http.parse_retry_after("soon") is None
    assert http.parse_retry_after(None) is None


# --------------------------------------------------------------------------- downloads


def test_download_streams_to_part_then_renames(tmp_path):
    body = b"x" * (3 * http.CHUNK + 5)
    c, _ = client(FakeOpener(FakeResponse(200, body)))
    dest = tmp_path / "sub" / "file.txt"
    result = c.download(URL, dest, expected_size=len(body))
    assert dest.read_bytes() == body
    assert result.sha256 == http.sha256_file(dest) and result.size == len(body)
    assert not list(dest.parent.glob("*.part"))


def test_partial_download_never_becomes_the_final_file(tmp_path):
    dest = tmp_path / "file.txt"
    dest.write_bytes(b"good old data")
    short = [FakeResponse(200, b"a" * 50, length=100) for _ in range(3)]
    c, waits = client(FakeOpener(*short), retries=2)
    with pytest.raises(IncompleteDownloadError, match="got 50 bytes, server said 100"):
        c.download(URL, dest)
    assert dest.read_bytes() == b"good old data"
    assert not list(tmp_path.glob("*.part"))
    assert len(waits) == 2


def test_timeout_mid_stream_is_retried_and_leaves_no_part(tmp_path):
    opener = FakeOpener(FakeResponse(200, b"abcdef", fail_after=3), FakeResponse(200, b"abcdef"))
    c, waits = client(opener)
    dest = tmp_path / "file.txt"
    assert c.download(URL, dest).size == 6
    assert dest.read_bytes() == b"abcdef" and waits == [1.0]

    opener = FakeOpener(FakeResponse(200, b"abcdef", fail_after=3))
    c, _ = client(opener, retries=0)
    with pytest.raises(IncompleteDownloadError, match="TimeoutError"):
        c.download(URL, tmp_path / "other.txt")
    assert not (tmp_path / "other.txt").exists()
    assert not list(tmp_path.glob("*.part"))


def test_checksum_or_size_mismatch_is_refused(tmp_path):
    c, _ = client(FakeOpener(FakeResponse(200, b"abc")), retries=0)
    with pytest.raises(IncompleteDownloadError, match="checksum"):
        c.download(URL, tmp_path / "f", sha256="0" * 64)
    c, _ = client(FakeOpener(FakeResponse(200, b"abc")), retries=0)
    with pytest.raises(IncompleteDownloadError, match="registry expects 4"):
        c.download(URL, tmp_path / "f", expected_size=4)
    assert not (tmp_path / "f").exists()


def test_bad_request_during_download_leaves_no_part(tmp_path):
    c, _ = client(FakeOpener(http_error(400, b"bad")))
    with pytest.raises(BadRequestError):
        c.download(URL, tmp_path / "f")
    assert list(tmp_path.iterdir()) == []


def test_disk_floor_refuses_before_any_request(tmp_path):
    opener = FakeOpener(FakeResponse(200, b"x"))
    c = HttpClient(opener=opener, sleep=lambda _s: None, free_bytes=lambda _p: 14 * 10**9)
    with pytest.raises(DiskSpaceError, match="floor is 15 GB"):
        c.download(URL, tmp_path / "f")
    assert opener.requests == []
    # Enough free space now, but the file itself would push it under the floor.
    c = HttpClient(opener=opener, sleep=lambda _s: None, free_bytes=lambda _p: 16 * 10**9)
    with pytest.raises(DiskSpaceError):
        c.download(URL, tmp_path / "f", expected_size=2 * 10**9)


def test_check_free_space_passes_above_floor(tmp_path):
    assert http.check_free_space(tmp_path, free_bytes=lambda _p: 20 * 10**9) == 20 * 10**9


# --------------------------------------------------------------------------- cache and keys


def test_cache_reuses_file_and_ignores_the_key(tmp_path):
    opener = FakeOpener(FakeResponse(200, b"rows"))
    c, _ = client(opener, tmp_path)
    first = c.cached(URL, {"get": "A", "key": FAKE_KEY}, suffix=".json")
    second = c.cached(URL, {"get": "A", "key": "another-key-999"}, suffix=".json")
    assert len(opener.requests) == 1
    assert first.path == second.path and second.from_cache
    assert FAKE_KEY not in first.path.name and FAKE_KEY not in first.url
    assert first.path.read_bytes() == b"rows"


def test_request_key_rules():
    k = http.request_key
    assert k(URL, {"a": 1, "b": 2}) == k(URL, {"b": 2, "a": 1})
    assert k(URL, {"a": 1}) == k(URL, {"a": 1, "key": FAKE_KEY})
    assert k(URL + "?a=1&key=x") == k(URL, {"a": "1"})
    assert k(URL, {"a": 1}) != k(URL, {"a": 2})
    assert len(k(URL)) == 64


def test_redact_hides_keys_everywhere(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", FAKE_KEY)
    text = f"GET https://api.example.test/x?get=A&key=abc123&for=tract:* failed; saw {FAKE_KEY}"
    out = http.redact(text)
    assert "abc123" not in out and FAKE_KEY not in out
    assert "key=REDACTED&for=tract:*" in out
    assert str(FetchError(f"boom {FAKE_KEY}")) == "boom REDACTED"


def test_census_key_params_reads_env_only():
    assert http.census_key_params({}) == {}
    assert http.census_key_params({"CENSUS_API_KEY": " k "}) == {"key": "k"}


def test_ssl_context_uses_system_store():
    import truststore

    assert isinstance(http.ssl_context(), truststore.SSLContext)


# --------------------------------------------------------------------------- range reads


class RangeServer:
    """A fake web server holding one file in memory and honouring HEAD and Range."""

    def __init__(self, blob: bytes, honour_range: bool = True):
        self.blob = blob
        self.honour_range = honour_range
        self.sent = 0
        self.requests = []

    def open(self, req, data=None, timeout=None):
        self.requests.append(req)
        if req.get_method() == "HEAD":
            return FakeResponse(200, b"", length=len(self.blob))
        rng = req.get_header("Range")
        if rng and self.honour_range:
            start, end = (int(x) for x in rng.removeprefix("bytes=").split("-"))
            part = self.blob[start : end + 1]
            self.sent += len(part)
            return FakeResponse(206, part)
        self.sent += len(self.blob)
        return FakeResponse(200, self.blob)


def make_zip() -> tuple[bytes, dict[str, bytes]]:
    rng = random.Random(42)  # noqa: S311 - filler bytes for a test, not a secret
    members = {
        "big_geometry.shp": rng.randbytes(2_000_000),
        "table.dbf": b"GEOID20,HOUSING20\n" + b"420010301001000,12\n" * 2000,
        "notes.txt": b"stored, not compressed",
        "empty.cpg": b"",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("big_geometry.shp", members["big_geometry.shp"], zipfile.ZIP_STORED)
        zf.writestr("table.dbf", members["table.dbf"], zipfile.ZIP_DEFLATED)
        zf.writestr("notes.txt", members["notes.txt"], zipfile.ZIP_STORED)
        zf.writestr("empty.cpg", b"", zipfile.ZIP_DEFLATED)
        zf.writestr("folder/", b"")
        zf.comment = b"a zip comment"
    return buf.getvalue(), members


def test_range_read_lists_members_and_fetches_only_one():
    blob, members = make_zip()
    server = RangeServer(blob)
    c, _ = client(server)
    listing = c.remote_zip_members(URL)
    assert set(listing) == set(members)  # the folder entry is skipped
    assert listing["table.dbf"].method == 8

    server.sent = 0
    assert c.read_zip_member(URL, "table.dbf") == members["table.dbf"]
    assert server.sent < len(blob) * 0.05  # the 2 MB member was never transferred
    assert all(r.get_header("Range") or r.get_method() == "HEAD" for r in server.requests)
    assert c.read_zip_member(URL, "notes.txt") == members["notes.txt"]
    assert c.read_zip_member(URL, "empty.cpg") == b""


def test_range_read_member_to_file(tmp_path):
    blob, members = make_zip()
    c, _ = client(RangeServer(blob))
    result = c.download_zip_member(URL, "table.dbf", tmp_path / "t.dbf")
    assert (tmp_path / "t.dbf").read_bytes() == members["table.dbf"]
    assert result.size == len(members["table.dbf"]) and result.url.endswith("#table.dbf")
    assert not list(tmp_path.glob("*.part"))


def test_range_read_errors():
    blob, _ = make_zip()
    c, _ = client(RangeServer(blob, honour_range=False))
    with pytest.raises(RangeNotSupportedError):
        c.remote_zip_members(URL)
    c, _ = client(RangeServer(blob))
    with pytest.raises(FetchError, match="no member named"):
        c.read_zip_member(URL, "missing.dbf")
    c, _ = client(RangeServer(b"not a zip at all" * 10))
    with pytest.raises(FetchError, match="no zip end record"):
        c.remote_zip_members(URL)


def test_corrupt_member_fails_crc_check():
    blob, members = make_zip()
    start = blob.index(members["notes.txt"])
    broken = blob[:start] + b"S" + blob[start + 1 :]
    c, _ = client(RangeServer(broken))
    with pytest.raises(IncompleteDownloadError, match="CRC"):
        c.read_zip_member(URL, "notes.txt")


def test_zip64_extra_field_supplies_large_sizes():
    big = 5 * 2**32
    extra = struct.pack("<HHQQQ", 0x0001, 24, big, big - 1, big + 7)
    assert http._zip64_extra(extra, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF) == (big, big - 1, big + 7)
    assert http._zip64_extra(b"", 10, 9, 8) == (10, 9, 8)


def test_no_real_network_module_is_touched(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("tests must not open real connections")

    monkeypatch.setattr("socket.create_connection", refuse)
    c, _ = client(FakeOpener(FakeResponse(200, b"ok")))
    assert c.get_bytes(URL) == b"ok"


def test_part_name_is_next_to_destination(tmp_path):
    dest = Path(tmp_path) / "a.b.txt"
    c, _ = client(FakeOpener(FakeResponse(200, b"a" * 5, length=9)), retries=0)
    with pytest.raises(IncompleteDownloadError):
        c.download(URL, dest)
    assert sorted(p.name for p in tmp_path.iterdir()) == []
