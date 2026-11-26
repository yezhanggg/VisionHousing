# Challenge checklist

Each pass/fail requirement of the Challenge, and where this repository meets it.

| Requirement | Where it is met | Status |
|---|---|---|
| Approved open-source licence | [`LICENSE`](../LICENSE) (Apache-2.0, full text) and [`NOTICE`](../NOTICE) | Done |
| Runs from documented setup with no hidden steps | [`README.md`](../README.md), [`USER_GUIDE.md`](USER_GUIDE.md), the `Makefile` (`make setup`, `make data`, `make app`), and `make clean-clone`, which copies the published files to an empty folder and runs setup and the checks there | Done |
| Automated tests of the core logic, with a documented command | `tests/`, run with `make test` (offline, on the synthetic test state) or `make verify` | Done |
| Tests pass on a clean checkout | `make clean-clone` and the CI workflow `.github/workflows/ci.yml`, which on every push runs the same checks as `make verify` (lint, licence audit, secret scan, `make docs`, `make sbom-check`, the offline tests and the browser checks) | Done |
| No committed secrets, secret scanning on | `scripts/secret_scan.py` (inside `make verify`), `.gitignore`, `.env.example` with empty values, [`SECURITY.md`](../SECURITY.md). GitHub secret scanning and push protection are repository settings | Local scan done. <!-- TODO(Ye): confirm GitHub secret scanning and push protection are on, with zero open alerts --> |
| CodeQL code scanning | `.github/workflows/codeql.yml` (Python and JavaScript) | Workflow done. TODO(Ye): no open critical or high alert; each fixed or dismissed with a written reason (steps below) |
| Dependabot alerts and updates | `.github/dependabot.yml` (pip, npm, GitHub Actions); CI also fails on high or critical npm advisories (`npm audit --audit-level=high`) | Config done. TODO(Ye): turn on Dependabot alerts and security updates in the repository settings; no open critical or high alert (steps below) |
| Permissive dependencies only | `scripts/licence_audit.py` (inside `make verify`), full table in [`DISCLOSURES.md`](../DISCLOSURES.md) | Done |
| Software bill of materials | [`sbom/visionhousing.cdx.json`](../sbom/visionhousing.cdx.json) (CycloneDX), written by `scripts/sbom.py` (`make sbom`) from `uv.lock` and `web/package-lock.json`; `make sbom-check` (inside `make verify` and CI) fails if it is stale. The dependency table in [`DISCLOSURES.md`](../DISCLOSURES.md) lists every locked package with its version and licence | Done. GitHub's own export (Insights, Dependency graph, Export SBOM) can be attached to the submission as a second copy <!-- TODO(Ye): export it after the final push --> |
| Data fetched by script, no large datasets in git | `make data` (`visionhousing fetch` and `build`), `src/visionhousing/data/`, `data/README.md`, `.gitignore` (`data/` is ignored). `web/public/data/` holds only the tool's own saved results for the no-server site, kept under the export's size limit; it holds no source data | Done |
| Disclosures | [`DISCLOSURES.md`](../DISCLOSURES.md): AI tools, services, dependencies, datasets | Done. Generated tables are refreshed by `scripts/gen_docs_tables.py` |
| Data provenance | [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md), built from the source registry | Done. Generated tables are refreshed by `scripts/gen_docs_tables.py` |
| Every number in the documents comes from the engine | Results and assumption values sit in generated blocks written by `scripts/gen_docs_tables.py`; `make docs` fails if one is stale | Done. `make docs` runs inside `make verify` and in CI |
| Model & Assumptions Overview (at most 25 pages) and Policy Scenario Comparison (at most 4 pages) | [`MODEL_AND_ASSUMPTIONS.md`](MODEL_AND_ASSUMPTIONS.md), with the detail in [`TECHNICAL_APPENDIX.md`](TECHNICAL_APPENDIX.md); [`POLICY_SCENARIO_COMPARISON.md`](POLICY_SCENARIO_COMPARISON.md) | Done. Both checked by printing to PDF (US Letter, 1-inch margins, 11-pt text) |

## Repository settings only the owner can change

These are GitHub settings, not files, so no script in this repository can turn them on.

**Dependabot.** TODO(Ye):

1. On GitHub, open the repository, then **Settings → Advanced Security** (on some accounts: **Settings → Code security and analysis**).
2. Under **Dependabot**, click **Enable** for **Dependabot alerts**, then for **Dependabot security updates**. Version updates are already configured by `.github/dependabot.yml`.
3. Open **Security → Dependabot**. For each open alert: merge the fix pull request Dependabot opens, or dismiss it with a written reason. Merge or close the open version-update pull requests.
4. Done when the Dependabot tab shows no open critical or high alert.

**CodeQL.** TODO(Ye):

1. The workflow `.github/workflows/codeql.yml` runs on every push (advanced setup). In **Settings → Advanced Security → Code scanning**, leave default setup off; the workflow is the setup.
2. After the final push, open **Actions → CodeQL** and check the latest run succeeded.
3. Open **Security → Code scanning**, filter by severity *Critical* and *High*. Each alert must be fixed in code (then it closes on the next run) or dismissed with **Dismiss alert** and a written reason ("false positive" or "used in tests") that is also recorded in [`SECURITY.md`](../SECURITY.md).
4. Done when no critical or high alert is open.

## Sources at a glance

All data is public and downloaded by script from its publisher. None of it is stored in this repository. The full list, with version, access date, licence, cleaning steps and limits, is in [`DATA_PROVENANCE.md`](../DATA_PROVENANCE.md).

## Limitations at a glance

- The outcome is units **authorized by building permits**, not units completed or occupied. Some ADUs are permitted as additions and may not appear in permit statistics.
- The horizon is five years (2027 to 2031). Effects after year five are not modelled.
- Results are computed by census tract and reported by county, legislative district and state. The tool gives no parcel-level or address-level answer. District results are split from tracts and counties.
- The tool cannot say who will live in new units, what they will rent for, or whether local governments will comply. Approval delay is illustrative only.
- Take-up of a new permission is the most uncertain input. Its range is shown, not hidden.
- States with less complete data get wider ranges and borrow some calibrations. Every number shows its data tier.

The full list is in [`MODEL_AND_ASSUMPTIONS.md`](MODEL_AND_ASSUMPTIONS.md#5-limitations-and-known-edge-cases).
