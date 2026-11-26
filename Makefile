# VisionHousing — one-command workflow. Run `make help` for the list.

UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
STATE ?= PA
RUN := $(UV) run --locked
NPM ?= npm
DATA_OUT ?= web/public/data
# Tract profiles per state saved whole up to this many MB (before packing); over it they are saved
# compact. The folder's own budget (12 MB compressed, 60 MB ceiling) is in export_static.py.
TRACT_BUDGET_MB ?= 4

.DEFAULT_GOAL := help
.PHONY: help setup data test test-realdata lint format licence secrets verify app all clean-clone \
	web web-static dev export-static site

help:  ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

setup:  ## Install Python deps (uv.lock, all groups), the test browser and the web build tools (npm ci)
	$(UV) sync --locked --all-groups
	$(RUN) playwright install chromium
	cd web && $(NPM) ci

data:  ## Fetch sources and build the state data pack (STATE=PA)
	$(RUN) visionhousing fetch --state $(STATE)
	$(RUN) visionhousing build --state $(STATE)

test:  ## Run the test suite (offline; real-data checks excluded)
	$(RUN) pytest -m "not realdata"

test-realdata:  ## Check parsers against the live published files (downloads to data/raw)
	$(RUN) pytest -m realdata

lint:  ## Lint (with security and docstring rules) and check formatting
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format:  ## Apply ruff formatting and safe fixes
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

licence:  ## Fail if any dependency has a licence outside the allow-list
	$(RUN) python scripts/licence_audit.py

secrets:  ## Fail if a key, private key or home-folder path is in a published file
	$(RUN) python scripts/secret_scan.py

verify: lint test licence secrets docs sbom-check  ## Lint, tests, licence audit, secret scan, generated-docs check, SBOM check
	@echo "verify: all checks passed"

app: web  ## Build the interface, then serve the tool at http://localhost:8000
	$(RUN) visionhousing serve --host 127.0.0.1 --port 8000

web:  ## Build the interface into web/dist (Vite)
	cd web && $(NPM) run build

dev:  ## Live-reload interface at http://127.0.0.1:5173 plus the engine on :8000 (Ctrl-C stops both)
	@trap 'kill 0' EXIT INT TERM; \
	$(RUN) visionhousing serve --host 127.0.0.1 --port 8000 & \
	cd web && $(NPM) run dev

export-static:  ## Save a state's default results as JSON for the static site (STATE=PA; tract profiles kept under TRACT_BUDGET_MB)
	$(RUN) visionhousing export-static --state $(STATE) --out $(DATA_OUT) --tract-budget-mb $(TRACT_BUDGET_MB)

web-static:  ## Build the no-server site (reads web/public/data) into web/dist
	cd web && $(NPM) run build:static

site: web-static  ## Build and preview the static site at http://127.0.0.1:4173 (no engine)
	cd web && $(NPM) run preview

all: setup data verify app  ## setup -> data -> verify -> app

clean-clone:  ## Copy published files to a temp folder and run setup + verify there
	$(RUN) python scripts/clean_clone_check.py

.PHONY: docs
docs:  ## Fail if a generated document table is stale
	$(RUN) python scripts/gen_docs_tables.py --check

.PHONY: test-unit test-browser sbom sbom-check
test-unit:  ## Run the offline tests without the browser checks (CI job "tests")
	$(RUN) pytest -m "not realdata" --ignore=tests/e2e

test-browser:  ## Run only the browser checks (needs npm ci and the Playwright browser)
	$(RUN) pytest -m "not realdata" tests/e2e

sbom:  ## Write the CycloneDX SBOM from uv.lock and web/package-lock.json to sbom/
	$(RUN) python scripts/sbom.py

sbom-check:  ## Fail if sbom/visionhousing.cdx.json is stale against the lockfiles
	$(RUN) python scripts/sbom.py --check
