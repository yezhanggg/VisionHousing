"""Data manifest and provenance table.

Plain-language summary
----------------------
After each download the fetch code records one line in ``data/manifest.json``:
which source it was (``id``), the exact URL (with any API key removed), the file's
SHA-256 checksum (a fingerprint that changes if a single byte changes), its size
in bytes, when it was fetched (``accessed``, in UTC) and which edition of the data
it is (``vintage``). Anyone can re-run the fetch and compare fingerprints to see
whether they have the same data.

:func:`provenance_rows` joins the manifest with the source registry and returns
one row per source with the columns the provenance table needs: source, version,
access date, licence, terms, cleaning steps and known limits. The documentation
and the interface both build their tables from these rows, so they cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from visionhousing.data.http import redact, sha256_file
from visionhousing.data.registry import Source, load_registry

ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "data" / "manifest.json"
SCHEMA_VERSION = 1
NOT_FETCHED = "not yet fetched"


class ManifestError(ValueError):
    """The manifest file is malformed."""


@dataclass(frozen=True)
class ManifestEntry:
    """One downloaded file: its source id, URL, checksum, size, access time and vintage."""

    id: str
    url: str
    sha256: str
    size: int
    accessed: str
    vintage: str
    file: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", redact(self.url))
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ManifestError(f"entry '{self.id}': sha256 must be 64 lowercase hex characters")
        if self.size < 0:
            raise ManifestError(f"entry '{self.id}': size must not be negative")


def now_utc() -> str:
    """Return the current UTC time as an ISO 8601 string to the second."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def entry_for_file(
    source_id: str,
    url: str,
    path: Path,
    vintage: str,
    *,
    accessed: str | None = None,
    root: Path | None = None,
) -> ManifestEntry:
    """Build a manifest entry for a file already on disk (checksum and size are measured)."""
    path = Path(path)
    shown = path.name  # never an absolute path: it would expose the user's folders
    if root is not None and path.resolve().is_relative_to(Path(root).resolve()):
        shown = path.resolve().relative_to(Path(root).resolve()).as_posix()
    return ManifestEntry(
        id=source_id,
        url=url,
        sha256=sha256_file(path),
        size=path.stat().st_size,
        accessed=accessed or now_utc(),
        vintage=vintage,
        file=shown,
    )


def _sorted(entries: Iterable[ManifestEntry]) -> list[ManifestEntry]:
    return sorted(entries, key=lambda e: (e.id, e.url, e.file))


def load_manifest(path: Path = MANIFEST_PATH) -> list[ManifestEntry]:
    """Read the manifest; a missing file means an empty manifest."""
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise ManifestError(f"{path.name}: expected schema version {SCHEMA_VERSION}")
    try:
        return _sorted(ManifestEntry(**row) for row in data.get("entries", []))
    except TypeError as err:
        raise ManifestError(f"{path.name}: bad entry ({err})") from None


def save_manifest(entries: Iterable[ManifestEntry], path: Path = MANIFEST_PATH) -> Path:
    """Write the manifest atomically (temporary file, then rename), sorted for stable diffs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA_VERSION, "entries": [asdict(e) for e in _sorted(entries)]}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp).replace(path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def record(entry: ManifestEntry, path: Path = MANIFEST_PATH) -> list[ManifestEntry]:
    """Add or replace one entry (matched on id, URL and file) and save the manifest."""
    kept = [
        e for e in load_manifest(path) if (e.id, e.url, e.file) != (entry.id, entry.url, entry.file)
    ]
    entries = _sorted([*kept, entry])
    save_manifest(entries, path)
    return entries


def manifest_hash(entries: Iterable[ManifestEntry]) -> str:
    """One fingerprint for the whole data set: SHA-256 over every entry's id, file and checksum.

    Access times are left out, so re-fetching identical data gives the same hash.
    """
    digest = hashlib.sha256()
    for e in _sorted(entries):
        digest.update(f"{e.id}\t{e.file}\t{e.url}\t{e.sha256}\n".encode())
    return digest.hexdigest()


# --------------------------------------------------------------------------- provenance


@dataclass(frozen=True)
class ProvenanceRow:
    """One row of the provenance table."""

    source: str
    version: str
    access_date: str
    licence: str
    terms: str
    cleaning: str
    limits: str
    id: str = ""
    publisher: str = ""
    tier: int = 0


def provenance_rows(
    sources: Mapping[str, Source] | None = None,
    entries: Iterable[ManifestEntry] | None = None,
) -> list[ProvenanceRow]:
    """Return one provenance row per registry source, in registry order.

    The access date is the latest ``accessed`` day in the manifest for that source,
    or "not yet fetched" when nothing has been downloaded yet.
    """
    sources = load_registry() if sources is None else sources
    entries = load_manifest() if entries is None else list(entries)
    latest: dict[str, str] = {}
    for e in entries:
        if e.accessed > latest.get(e.id, ""):
            latest[e.id] = e.accessed
    rows = []
    for s in sources.values():
        rows.append(
            ProvenanceRow(
                source=f"{s.name} ({s.publisher})",
                version=s.vintage,
                access_date=latest[s.id][:10] if s.id in latest else NOT_FETCHED,
                licence=s.licence,
                terms=s.terms_url,
                cleaning=s.cleaning or "None beyond parsing.",
                limits=s.limits,
                id=s.id,
                publisher=s.publisher,
                tier=s.tier,
            )
        )
    return rows
