# Data fetch: network layer, source registry and manifest

Code: `src/visionhousing/data/http.py`, `registry.py`, `manifest.py`, `sources.yaml`.
Tests: `tests/test_http.py`, `tests/test_registry.py`, `tests/test_manifest.py`.

## What it does, in plain words

Every input the tool uses is downloaded by script from the agency that publishes
it. Nothing is stored in the repository. These three modules make that download
safe, repeatable and traceable.

1. **`sources.yaml`** lists every source once: who publishes it, the web address,
   the edition used, the licence, the known limits and so on.
2. **`http.py`** downloads files carefully: it retries when a server is busy, stops
   at once when a request is wrong, never leaves a half-finished file behind, and
   never prints an API key.
3. **`manifest.py`** writes `data/manifest.json`, a list of every file downloaded
   with its fingerprint (SHA-256 checksum), size, access time and edition. It also
   builds the provenance table that the documentation and the interface show.

## Terms used

| Term | Meaning |
|---|---|
| HTTP status | A three-digit code the server sends back. 200 = here it is; 204 = nothing to send; 206 = here is the part you asked for; 400 = your request is wrong; 429 = too many requests, slow down; 5xx = the server has a problem. |
| Retry with exponential backoff | Try again after 1 s, then 2 s, then 4 s, and so on, up to a cap. |
| `Retry-After` | A header in which the server says how long to wait. It is used when present. |
| Timeout | The longest the code waits for the server before giving up on that attempt (60 s by default). |
| `.part` file | The download is written under `name.part`, then renamed to `name` in one step only after it is checked. A failed download never replaces a good file. |
| SHA-256 | A 64-character fingerprint of a file. Any change to the file changes it. |
| Range request | Asking a server for bytes *a* to *b* of a file instead of the whole file. |
| Central directory | The index at the end of a zip file that lists each member and where it starts. |
| Redaction | Replacing the API key with `REDACTED` in every message, error and saved URL. |

## Network layer (`http.py`)

- **Standard library only.** Requests go through `urllib` with a TLS context from
  `truststore`, which uses the operating system's certificate store. No
  third-party HTTP library is used (the common ones pull in a certificate bundle
  under a licence the project avoids).
- **User-Agent.** Every request says `visionhousing (open-source research tool)`.
- **Status handling.**

  | Answer | What the code does |
  |---|---|
  | 200, 206 | Use the body. |
  | 204 | Treat as empty (`b""`, or an empty file). |
  | 400 | Fail at once with the server's message. No retry. |
  | 429, 500–599 | Wait (the server's `Retry-After`, else 1, 2, 4, 8 s; never more than 120 s) and retry, 4 extra attempts by default. |
  | Timeout or dropped connection | Same as 429/5xx. |
  | Other 4xx (404 and so on) | Fail at once with the message. |

- **Safe downloads.** `HttpClient.download` streams to `name.part` in 1 MB pieces
  while computing the SHA-256. It then checks the byte count against the server's
  `Content-Length`, and against the registry's expected size and checksum when
  given. Only then is the file renamed. Any failure deletes the `.part` file and
  leaves an existing good file untouched.
- **Disk floor.** A download refuses to start when free space, minus the expected
  size, would fall below 15 GB (the project's safety margin).
- **Cache.** `HttpClient.cached` stores answers under a name that is the SHA-256 of
  the URL plus its sorted parameters. The `key` parameter is left out, so the cache
  name does not depend on, or reveal, the key.
- **API key.** The optional Census key is read only from the `CENSUS_API_KEY`
  environment variable (`census_key_params`). `redact` removes it, and any
  `key=...` in a URL, from every message and exception.
- **One file out of a big zip.** `remote_zip_members` asks for the file size (HEAD),
  reads the last ≈ 64 KB to find the end-of-central-directory record (and the zip64
  record for very large zips), then reads the central directory with one range
  request. `read_zip_member` reads the member's local header, fetches just its
  compressed bytes, inflates them and checks the size and CRC-32 checksum. For the
  2020 Pennsylvania block file this reads about 46 MB of a 409 MB zip.
- **Testable.** The opener, the sleep function and the free-space function are
  arguments, so tests use fakes: no network and no real waiting.

## Source registry (`registry.py`, `sources.yaml`)

Each entry has these fields. Unknown fields are rejected so typos are caught.

| Field | Meaning |
|---|---|
| `id` | Short lower-case name, used in code and in the manifest |
| `name`, `publisher` | What the dataset is and who publishes it |
| `url_pattern` | `https://` address; `{year}`, `{state_fips}`, `{table}` and so on are filled at fetch time |
| `vintage` | The edition used |
| `geography` | Levels covered (county, tract, block, …) |
| `licence`, `terms_url` | Legal status and where the terms are stated |
| `expected_size` | Approximate bytes per file, or empty for API answers and web pages |
| `parser` | `module.function` that reads the file (written with each source's own task) |
| `limits` | What the data cannot tell us |
| `tier` | 0 = every state, 1 = many states, 2 = local refinement only |
| `access` | `file`, `api`, `range` (one zip member), `feature_service` (paged map service) or `page` |
| `member` | For `range` sources: the file inside the zip |
| `cleaning` | Cleaning steps, shown in the provenance table |
| `checked_on` | Date the address was last confirmed to answer |
| `notes` | Anything else worth knowing |

`load_registry()` checks every entry. A bad entry stops the load with a message
that starts `source '<id>': …` and names the field. **Adding a source** means one
entry here plus one parser function.

### Seed sources (addresses checked 2026-11-22)

Census Building Permits Survey county files (1990–2025); ACS 2020–2024 5-year
table-based summary files (no key, the default ACS path) and the Census Data API;
2020 cartographic boundary files for tracts and counties; TIGER 2020 tabulation
blocks (attribute table only, by range read); FHFA House Price Index for counties
(spreadsheet) and tracts (CSV); Freddie Mac weekly mortgage rates; HUD Fair Market
Rents (direct file); National Transit Map stops (paged map-service query); and the
Pennsylvania HB 2186 and SB 1346 bill pages.

Two findings from the check:

- The Census Data API answered a call **without a key** with a "Missing Key" page.
  The summary files give the same estimates without a key, so they are the default.
- The HUD site answered scripted requests with status 202 and an empty body (a bot
  check). The Fair Market Rent address follows HUD's published naming and must be
  confirmed by hand before the first fetch.

## Manifest and provenance (`manifest.py`)

`data/manifest.json` (git-ignored, rebuilt by `make data`):

```json
{
  "schema": 1,
  "entries": [
    {"id": "bps_county_annual", "url": "https://www2.census.gov/econ/bps/County/co2024a.txt",
     "sha256": "<64 hex characters>", "size": 375214,
     "accessed": "2026-11-22T18:00:00+00:00", "vintage": "2024", "file": "raw/co2024a.txt"}
  ]
}
```

- `entry_for_file` measures the checksum and size of a file already on disk.
- `record` adds or replaces one entry and saves the manifest atomically.
- `manifest_hash` gives one fingerprint for the whole data set; access times are
  left out, so fetching the same data again gives the same hash.
- `file` is always relative (never an absolute path, which would expose a user's
  folders), and `url` is always redacted.
- `provenance_rows(sources, entries)` returns one row per registry source with:
  source, version, access date (latest in the manifest, or "not yet fetched"),
  licence, terms, cleaning and limits. `DATA_PROVENANCE.md` and the interface are
  built from these rows.

## Limits

- Range reads need a server that honours `Range`; if it does not, the code stops
  with `RangeNotSupportedError` instead of downloading the whole file silently.
- Zip members using compression other than "stored" or "deflate" are refused.
- Mid-stream failures restart the download from the beginning (no resume).
- Parsers for each source are separate tasks; the registry names them in advance.
