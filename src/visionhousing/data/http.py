"""Network layer: every download in the project goes through this module.

Plain-language summary
----------------------
- It uses only Python's standard library (``urllib``) plus ``truststore``, which
  lets Python trust the same certificates as the operating system. No third-party
  HTTP library is needed.
- A download is written to ``<name>.part`` first. Only after the size (and, when
  known, the SHA-256 checksum) is checked is it renamed to its final name in one
  step. A failed or cut-off download never replaces a good file.
- Busy or broken servers (HTTP 429 "too many requests" and 5xx "server error")
  are retried with waits that double each time (exponential backoff). When the
  server says how long to wait (the ``Retry-After`` header), that wait is used.
- A 400 "bad request" fails at once and shows the server's explanation, because
  retrying a wrong request never helps.
- A 204 "no content" answer means "nothing found" and gives empty bytes.
- Every request has a timeout, so a dead server cannot hang a run.
- Answers can be cached on disk under a name made from a hash of the URL and its
  parameters. The API key is left out of that hash, so a cached file does not
  depend on (or reveal) whose key fetched it.
- The Census API key is removed ("redacted") from every message and error.
- :func:`remote_zip_members` and :class:`HttpClient.read_zip_member` read one file
  out of a large zip on a web server using HTTP range requests (asking for a byte
  range rather than the whole file). Only the zip's index and the one member are
  transferred.
- Downloads refuse to start when free disk space would fall below 15 GB.

Everything that touches the network or the clock (the opener, the sleep function
and the free-space function) can be replaced, so tests run with no network and no
real waiting.
"""

from __future__ import annotations

import email.utils
import hashlib
import os
import re
import shutil
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import truststore

USER_AGENT = "visionhousing (open-source research tool)"
DEFAULT_TIMEOUT = 60.0  # seconds, for connecting and for each read
DEFAULT_RETRIES = 4  # extra attempts after the first one
BACKOFF_BASE = 1.0  # first wait in seconds; doubles each retry
MAX_WAIT = 120.0  # no single wait is longer than this, whatever Retry-After says
CHUNK = 1 << 20  # bytes read per step while streaming
MIN_FREE_GB = 15.0  # free-disk floor (project rule)
KEY_ENV = "CENSUS_API_KEY"
SECRET_PARAMS = frozenset({"key", "api_key", "apikey", "token"})
REDACTED = "REDACTED"
ERROR_BODY_CHARS = 1000  # how much of an error body is shown

_SECRET_QUERY = re.compile(
    r"(?i)(\b(?:" + "|".join(sorted(SECRET_PARAMS)) + r")=)[^&\s#'\"]+",
)


# --------------------------------------------------------------------------- errors


def redact(text: object, secrets: tuple[str, ...] = ()) -> str:
    """Return ``text`` as a string with every API key replaced by ``REDACTED``.

    Removes ``key=...`` (and similar) values from URLs and query strings, the
    current value of the ``CENSUS_API_KEY`` environment variable wherever it
    appears, and any extra literal ``secrets`` passed in.
    """
    out = _SECRET_QUERY.sub(r"\1" + REDACTED, str(text))
    values = [*secrets, os.environ.get(KEY_ENV, "")]
    for value in values:
        if value and len(value) >= 4:
            out = out.replace(value, REDACTED)
    return out


class FetchError(RuntimeError):
    """A download failed. The message is always redacted."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        self.status = status
        self.body = redact(body)
        super().__init__(redact(message))


class BadRequestError(FetchError):
    """The server answered 400: the request itself is wrong, so it is not retried."""


class RetriesExhaustedError(FetchError):
    """The server kept failing (429, 5xx, timeouts) after every retry."""


class IncompleteDownloadError(FetchError):
    """Fewer (or other) bytes arrived than the server or the registry promised."""


class DiskSpaceError(FetchError):
    """Free disk space is below the safety floor, so the download was refused."""


class RangeNotSupportedError(FetchError):
    """The server ignored an HTTP range request, so a partial read is impossible."""


# --------------------------------------------------------------------------- helpers


class Opener(Protocol):
    """Anything with ``open(request, timeout=...)`` returning a response, like urllib's."""

    def open(self, fullurl: urllib.request.Request, data: Any = None, timeout: float = ...) -> Any:
        """Send ``fullurl`` and return a file-like response with ``status`` and ``headers``."""
        ...


def ssl_context() -> ssl.SSLContext:
    """Return a TLS context that trusts the operating system's certificate store."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def default_opener() -> urllib.request.OpenerDirector:
    """Return a urllib opener that uses the operating system's certificates."""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))


def free_bytes_on(folder: Path) -> int:
    """Return free bytes on the disk holding ``folder`` (its nearest existing parent)."""
    folder = Path(folder).resolve()
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return shutil.disk_usage(folder).free


def check_free_space(
    folder: Path,
    needed_bytes: int = 0,
    *,
    min_gb: float = MIN_FREE_GB,
    free_bytes: Callable[[Path], int] = free_bytes_on,
) -> int:
    """Refuse (raise :class:`DiskSpaceError`) when a download would leave less than ``min_gb``.

    ``needed_bytes`` is the expected size of the download (0 when unknown). Returns
    the free bytes found.
    """
    free = free_bytes(Path(folder))
    floor = min_gb * 1e9
    if free - needed_bytes < floor:
        raise DiskSpaceError(
            f"refusing to download: {free / 1e9:.1f} GB free, {needed_bytes / 1e9:.2f} GB "
            f"needed, floor is {min_gb:.0f} GB"
        )
    return free


def build_url(url: str, params: Mapping[str, Any] | None = None) -> str:
    """Join ``url`` and query ``params`` (kept in the given order) into one URL."""
    if not params:
        return url
    sep = "&" if urllib.parse.urlsplit(url).query else "?"
    return url + sep + urllib.parse.urlencode(list(params.items()), safe=",:*")


def request_key(url: str, params: Mapping[str, Any] | None = None) -> str:
    """Return the cache key for a request: SHA-256 of the URL plus sorted parameters.

    Secret parameters (``key`` and similar) are dropped first, both from ``params``
    and from any query already in ``url``, so the key never affects the cache name.
    """
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in SECRET_PARAMS
    ]
    query += [(str(k), str(v)) for k, v in (params or {}).items() if k.lower() not in SECRET_PARAMS]
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    text = base + "\n" + "\n".join(f"{k}={v}" for k, v in sorted(query))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def census_key_params(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return ``{"key": ...}`` when ``CENSUS_API_KEY`` is set, else an empty dict."""
    value = (os.environ if env is None else env).get(KEY_ENV, "").strip()
    return {"key": value} if value else {}


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Turn a ``Retry-After`` header (seconds or an HTTP date) into seconds to wait."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def sha256_file(path: Path) -> str:
    """Return the SHA-256 checksum of a file, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _status(resp: Any) -> int:
    return int(getattr(resp, "status", None) or getattr(resp, "code", None) or 200)


def _header(resp: Any, name: str) -> str | None:
    headers = getattr(resp, "headers", None)
    return headers.get(name) if headers is not None else None


def _close(resp: Any) -> None:
    close = getattr(resp, "close", None)
    if close:
        close()


_TRANSIENT = (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError)


# --------------------------------------------------------------------------- zip structures


@dataclass(frozen=True)
class ZipMember:
    """One entry of a remote zip's central directory (its index)."""

    name: str
    method: int  # 0 = stored, 8 = deflated
    crc32: int
    compressed_size: int
    size: int
    header_offset: int


EOCD_SIG = b"PK\x05\x06"
ZIP64_LOCATOR_SIG = b"PK\x06\x07"
ZIP64_EOCD_SIG = b"PK\x06\x06"
CD_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"
EOCD_MAX_TAIL = 22 + 0xFFFF + 20  # record + longest comment + zip64 locator


def _parse_central_directory(data: bytes) -> dict[str, ZipMember]:
    members: dict[str, ZipMember] = {}
    pos = 0
    while pos + 46 <= len(data) and data[pos : pos + 4] == CD_SIG:
        (_, _, _, _, method, _, _, crc, csize, usize, nlen, xlen, clen, _, _, _, offset) = (
            struct.unpack("<4s6H3L5H2L", data[pos : pos + 46])
        )
        name = data[pos + 46 : pos + 46 + nlen].decode("utf-8", errors="replace")
        extra = data[pos + 46 + nlen : pos + 46 + nlen + xlen]
        usize, csize, offset = _zip64_extra(extra, usize, csize, offset)
        if not name.endswith("/"):
            members[name] = ZipMember(name, method, crc, csize, usize, offset)
        pos += 46 + nlen + xlen + clen
    return members


def _zip64_extra(extra: bytes, usize: int, csize: int, offset: int) -> tuple[int, int, int]:
    """Read 64-bit sizes from the zip64 extra field when the 32-bit ones are maxed out."""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[pos : pos + 4])
        body = extra[pos + 4 : pos + 4 + length]
        if tag == 0x0001:
            values = list(struct.unpack(f"<{len(body) // 8}Q", body[: len(body) // 8 * 8]))
            if usize == 0xFFFFFFFF and values:
                usize = values.pop(0)
            if csize == 0xFFFFFFFF and values:
                csize = values.pop(0)
            if offset == 0xFFFFFFFF and values:
                offset = values.pop(0)
        pos += 4 + length
    return usize, csize, offset


# --------------------------------------------------------------------------- client


@dataclass(frozen=True)
class DownloadResult:
    """What a finished download produced: where it is, its checksum and size."""

    path: Path
    sha256: str
    size: int
    url: str  # redacted
    from_cache: bool = False


@dataclass
class HttpClient:
    """Fetches URLs with retries, timeouts, a disk floor, an optional cache and redaction.

    Args:
        opener: object with ``open(request, timeout=...)``; defaults to urllib with
            the operating system's certificates. Tests pass a fake.
        sleep: function used to wait between retries; tests pass a recorder.
        free_bytes: function returning free bytes for a folder; tests pass a stub.
        timeout: seconds allowed for connecting and for each read.
        retries: extra attempts after the first for 429, 5xx and network errors.
        backoff: first wait in seconds; each retry doubles it, capped at ``max_wait``.
        max_wait: the longest single wait, including a server's ``Retry-After``.
        min_free_gb: downloads refuse to start below this much free disk.
        cache_dir: folder for cached answers; ``None`` disables :meth:`cached`.
    """

    opener: Opener | None = None
    sleep: Callable[[float], None] = time.sleep
    free_bytes: Callable[[Path], int] = free_bytes_on
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff: float = BACKOFF_BASE
    max_wait: float = MAX_WAIT
    min_free_gb: float = MIN_FREE_GB
    cache_dir: Path | None = None
    user_agent: str = USER_AGENT
    waits: list[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.opener is None:
            self.opener = default_opener()

    # ----------------------------------------------------------------- core request

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        told = parse_retry_after(retry_after)
        wait = told if told is not None else self.backoff * (2**attempt)
        wait = min(wait, self.max_wait)
        self.waits.append(wait)
        self.sleep(wait)

    def open(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        ok: tuple[int, ...] = (200, 204, 206),
    ) -> Any:
        """Send one request with retries and return the open response.

        Raises :class:`BadRequestError` on 400 (with the body), retries 429 and 5xx
        (honouring ``Retry-After``) and network errors, and raises
        :class:`RetriesExhaustedError` when every attempt failed. The caller must
        close the response (use it in a ``with`` block or call ``close``).
        """
        full = build_url(url, params)
        shown = redact(full)
        req = urllib.request.Request(  # noqa: S310 - callers pass https URLs from the registry
            full,
            method=method,
            headers={"User-Agent": self.user_agent, **(headers or {})},
        )
        last = ""
        for attempt in range(self.retries + 1):
            try:
                resp = self.opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as err:
                resp = err
            except _TRANSIENT as err:
                last = f"{type(err).__name__}: {err}"
                if attempt < self.retries:
                    self._wait(attempt, None)
                    continue
                break
            status = _status(resp)
            if status in ok:
                return resp
            body = _read_body(resp)
            if status == 400:
                raise BadRequestError(
                    f"HTTP 400 bad request for {shown}: {body}", status=400, body=body
                )
            if status == 429 or status >= 500:
                last = f"HTTP {status}: {body[:200]}"
                if attempt < self.retries:
                    self._wait(attempt, _header(resp, "Retry-After"))
                    continue
                break
            raise FetchError(f"HTTP {status} for {shown}: {body}", status=status, body=body)
        raise RetriesExhaustedError(
            f"gave up on {shown} after {self.retries + 1} attempts; last error: {last}"
        )

    def get_bytes(self, url: str, params: Mapping[str, Any] | None = None) -> bytes:
        """Fetch a small answer into memory. A 204 "no content" answer gives ``b""``."""
        for attempt in range(self.retries + 1):
            resp = self.open(url, params)
            try:
                if _status(resp) == 204:
                    return b""
                data = resp.read()
                expected = _header(resp, "Content-Length")
                if expected and expected.isdigit() and len(data) != int(expected):
                    raise IncompleteDownloadError(
                        f"{build_url(url, params)}: got {len(data)} bytes, expected {expected}"
                    )
                return data
            except (IncompleteDownloadError, *_TRANSIENT) as err:
                if attempt >= self.retries:
                    raise _as_incomplete(err, build_url(url, params)) from None
                self._wait(attempt, None)
            finally:
                _close(resp)
        raise AssertionError("unreachable")  # pragma: no cover

    # ----------------------------------------------------------------- downloads

    def download(
        self,
        url: str,
        dest: Path,
        params: Mapping[str, Any] | None = None,
        *,
        expected_size: int | None = None,
        sha256: str | None = None,
    ) -> DownloadResult:
        """Stream a URL to ``dest`` safely and return its checksum and size.

        Writes ``dest.part`` first, checks the byte count against the server's
        ``Content-Length`` (and ``expected_size`` / ``sha256`` when given), then
        renames it to ``dest`` in one step. On any failure the ``.part`` file is
        deleted and an existing ``dest`` is left as it was. A 204 answer writes an
        empty file.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        check_free_space(
            dest.parent, expected_size or 0, min_gb=self.min_free_gb, free_bytes=self.free_bytes
        )
        shown = redact(build_url(url, params))
        part = dest.with_name(dest.name + ".part")
        for attempt in range(self.retries + 1):
            try:
                digest, size = self._stream_once(url, params, part)
                if expected_size is not None and size != expected_size:
                    raise IncompleteDownloadError(
                        f"{shown}: got {size} bytes, registry expects {expected_size}"
                    )
                if sha256 and digest != sha256.lower():
                    raise IncompleteDownloadError(f"{shown}: checksum mismatch")
                part.replace(dest)
                return DownloadResult(dest, digest, size, shown)
            except (IncompleteDownloadError, *_TRANSIENT) as err:
                part.unlink(missing_ok=True)
                if attempt >= self.retries:
                    raise _as_incomplete(err, shown) from None
                self._wait(attempt, None)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _stream_once(
        self, url: str, params: Mapping[str, Any] | None, part: Path
    ) -> tuple[str, int]:
        resp = self.open(url, params)
        digest = hashlib.sha256()
        size = 0
        try:
            with part.open("wb") as out:
                if _status(resp) != 204:
                    for block in iter(lambda: resp.read(CHUNK), b""):
                        out.write(block)
                        digest.update(block)
                        size += len(block)
            expected = _header(resp, "Content-Length")
            if _status(resp) != 204 and expected and expected.isdigit() and size != int(expected):
                raise IncompleteDownloadError(
                    f"{redact(build_url(url, params))}: got {size} bytes, server said {expected}"
                )
        finally:
            _close(resp)
        return digest.hexdigest(), size

    def cached(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        *,
        suffix: str = "",
        refresh: bool = False,
        expected_size: int | None = None,
    ) -> DownloadResult:
        """Download into the cache unless a file for the same request is already there.

        The cache file name is :func:`request_key` plus ``suffix``; the API key is
        not part of it.
        """
        if self.cache_dir is None:
            raise FetchError("no cache_dir set on this HttpClient")
        dest = Path(self.cache_dir) / f"{request_key(url, params)}{suffix}"
        if dest.exists() and not refresh:
            return DownloadResult(
                dest,
                sha256_file(dest),
                dest.stat().st_size,
                redact(build_url(url, params)),
                from_cache=True,
            )
        return self.download(url, dest, params, expected_size=expected_size)

    # ----------------------------------------------------------------- range reads

    def remote_size(self, url: str) -> int:
        """Return a remote file's size in bytes from a HEAD request."""
        resp = self.open(url, method="HEAD", ok=(200,))
        try:
            value = _header(resp, "Content-Length")
        finally:
            _close(resp)
        if not value or not value.isdigit():
            raise FetchError(f"{redact(url)}: the server did not report a size")
        return int(value)

    def read_range(self, url: str, start: int, end: int) -> bytes:
        """Return bytes ``start`` to ``end`` (inclusive) of a remote file.

        Raises :class:`RangeNotSupportedError` when the server sends the whole file
        instead (status 200 rather than 206).
        """
        want = end - start + 1
        for attempt in range(self.retries + 1):
            resp = self.open(url, headers={"Range": f"bytes={start}-{end}"}, ok=(206, 200))
            try:
                if _status(resp) != 206:
                    raise RangeNotSupportedError(
                        f"{redact(url)}: the server ignored the range request "
                        f"(status {_status(resp)})"
                    )
                data = resp.read()
                if len(data) != want:
                    raise IncompleteDownloadError(
                        f"{redact(url)}: range {start}-{end} gave {len(data)} of {want} bytes"
                    )
                return data
            except (IncompleteDownloadError, *_TRANSIENT) as err:
                if attempt >= self.retries:
                    raise _as_incomplete(err, redact(url)) from None
                self._wait(attempt, None)
            finally:
                _close(resp)
        raise AssertionError("unreachable")  # pragma: no cover

    def remote_zip_members(self, url: str) -> dict[str, ZipMember]:
        """List a remote zip's files by reading only its end records and index.

        Steps: ask for the file size; read the last few kilobytes to find the
        end-of-central-directory record (and the zip64 one for very large zips);
        then read the central directory (the zip's index) by one range request.
        """
        size = self.remote_size(url)
        tail_len = min(size, EOCD_MAX_TAIL)
        tail = self.read_range(url, size - tail_len, size - 1)
        at = tail.rfind(EOCD_SIG)
        if at < 0 or len(tail) - at < 22:
            raise FetchError(f"{redact(url)}: no zip end record found; is it a zip file?")
        _, _, _, _, entries, cd_size, cd_offset, _ = struct.unpack("<4s4H2LH", tail[at : at + 22])
        if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or entries == 0xFFFF:
            loc = tail[at - 20 : at] if at >= 20 else b""
            if loc[:4] != ZIP64_LOCATOR_SIG:
                raise FetchError(f"{redact(url)}: zip64 locator missing")
            (z64_offset,) = struct.unpack("<Q", loc[8:16])
            rec = self.read_range(url, z64_offset, z64_offset + 55)
            if rec[:4] != ZIP64_EOCD_SIG:
                raise FetchError(f"{redact(url)}: zip64 end record missing")
            cd_size, cd_offset = struct.unpack("<QQ", rec[40:56])
        tail_start = size - tail_len
        if cd_offset >= tail_start:
            directory = tail[cd_offset - tail_start : cd_offset - tail_start + cd_size]
        else:
            directory = self.read_range(url, cd_offset, cd_offset + cd_size - 1)
        return _parse_central_directory(directory)

    def _member_data_start(self, url: str, member: ZipMember) -> int:
        head = self.read_range(url, member.header_offset, member.header_offset + 29)
        if head[:4] != LOCAL_SIG:
            raise FetchError(f"{redact(url)}: bad local header for {member.name}")
        name_len, extra_len = struct.unpack("<HH", head[26:30])
        return member.header_offset + 30 + name_len + extra_len

    def read_zip_member(self, url: str, name: str) -> bytes:
        """Return one member of a remote zip, unpacked, without downloading the rest."""
        members = self.remote_zip_members(url)
        if name not in members:
            raise FetchError(f"{redact(url)}: no member named {name!r}")
        member = members[name]
        start = self._member_data_start(url, member)
        raw = (
            self.read_range(url, start, start + member.compressed_size - 1)
            if member.compressed_size
            else b""
        )
        return _unpack(raw, member, url)

    def download_zip_member(self, url: str, name: str, dest: Path) -> DownloadResult:
        """Save one member of a remote zip to ``dest`` (via ``.part`` and rename)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        members = self.remote_zip_members(url)
        if name not in members:
            raise FetchError(f"{redact(url)}: no member named {name!r}")
        check_free_space(
            dest.parent, members[name].size, min_gb=self.min_free_gb, free_bytes=self.free_bytes
        )
        data = self.read_zip_member(url, name)
        part = dest.with_name(dest.name + ".part")
        try:
            part.write_bytes(data)
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)
        return DownloadResult(
            dest, hashlib.sha256(data).hexdigest(), len(data), redact(url) + "#" + name
        )


def _unpack(raw: bytes, member: ZipMember, url: str) -> bytes:
    if member.method == 0:
        data = raw
    elif member.method == 8:
        inflater = zlib.decompressobj(-15)
        data = inflater.decompress(raw) + inflater.flush()
    else:
        raise FetchError(f"{redact(url)}: {member.name} uses compression {member.method}")
    if len(data) != member.size or (zlib.crc32(data) & 0xFFFFFFFF) != member.crc32:
        raise IncompleteDownloadError(f"{redact(url)}: {member.name} failed its size or CRC check")
    return data


def _read_body(resp: Any) -> str:
    try:
        raw = resp.read(ERROR_BODY_CHARS * 4) if hasattr(resp, "read") else b""
    except _TRANSIENT:
        raw = b""
    finally:
        _close(resp)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return redact(text.strip()[:ERROR_BODY_CHARS])


def _as_incomplete(err: BaseException, shown: str) -> FetchError:
    if isinstance(err, FetchError):
        return err
    return IncompleteDownloadError(f"{shown}: {type(err).__name__}: {err}")
