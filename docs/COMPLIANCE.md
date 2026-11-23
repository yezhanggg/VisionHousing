# Challenge checklist

Each pass/fail requirement of the Challenge, and where this repository meets it.

| Requirement | Where it is met | Status |
|---|---|---|
| Approved open-source licence | [`LICENSE`](../LICENSE) (Apache-2.0, full text) and [`NOTICE`](../NOTICE) | Done |
| Runs from documented setup with no hidden steps | [`README.md`](../README.md), the `Makefile`, `make clean-clone` | Done |
| Automated tests of the core logic, with a documented command | `tests/`, run with `make test` or `make verify` | Done |
| Tests pass on a clean checkout | `make clean-clone` and the CI workflow `.github/workflows/ci.yml` | Done |
| No committed secrets, secret scanning on | `scripts/secret_scan.py` (inside `make verify`), `.gitignore`, `.env.example` with empty values, [`SECURITY.md`](../SECURITY.md). GitHub secret scanning and push protection are repository settings | Local scan done. <!-- TODO(Ye): confirm GitHub secret scanning and push protection are on --> |
| CodeQL code scanning | `.github/workflows/codeql.yml` (Python and JavaScript) | Workflow done. <!-- TODO(Ye): confirm code scanning is enabled in repository settings --> |
| Dependabot alerts and updates | `.github/dependabot.yml` (pip, npm, GitHub Actions) | Config done. <!-- TODO(Ye): confirm Dependabot alerts are enabled --> |
| Permissive dependencies only | `scripts/licence_audit.py` (inside `make verify`), summary in [`DISCLOSURES.md`](../DISCLOSURES.md) | Done |
| Software bill of materials | Exported from GitHub (Insights, Dependency graph, Export SBOM). Summary in [`DISCLOSURES.md`](../DISCLOSURES.md) | **Planned** (export after the first public push) |
| Data fetched by script, no large datasets in git | `make data` (`visionhousing fetch` and `build`), `src/visionhousing/data/`, `data/README.md`, `.gitignore` | Done |
| Disclosures | [`DISCLOSURES.md`](../DISCLOSURES.md) | Done. Generated tables are refreshed before each release |
| Data provenance | [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md), built from the source registry | Done. Generated tables are refreshed before each release |

## Sources at a glance

All data is public and downloaded by script from its publisher. None of it is stored in this repository. The full list, with version, access date, licence, cleaning steps and limits, is in [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md).

## Limitations at a glance

- The outcome is units **authorized by building permits**, not units completed or occupied.
- The horizon is five years (2027 to 2031). Effects after year five are not modelled.
- Results are computed by census tract and reported by county and state. The tool gives no parcel-level or address-level answer.
- The tool cannot say who will live in new units, what they will rent for, or whether local governments will comply.
- Take-up of a new permission is the most uncertain input. Its range is shown, not hidden.
- States with less complete data get wider ranges. Every number shows its data tier.

The full list is in [`MODEL_AND_ASSUMPTIONS.md`](MODEL_AND_ASSUMPTIONS.md).
