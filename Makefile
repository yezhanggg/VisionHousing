# VisionHousing — one-command workflow. Run `make help` for the list.

UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
STATE ?= PA
RUN := $(UV) run --locked

.DEFAULT_GOAL := help
.PHONY: help setup data test test-realdata lint format licence secrets verify app all clean-clone

help:  ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

setup:  ## Install Python 3.12 deps from uv.lock (all groups) and the test browser
	$(UV) sync --locked --all-groups
	$(RUN) playwright install chromium
	$(RUN) python scripts/vendor_web.py

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

verify: lint test licence secrets  ## Lint + tests + licence audit + secret scan docs
	@echo "verify: all checks passed"

app:  ## Serve the tool at http://localhost:8000
	$(RUN) visionhousing serve --host 127.0.0.1 --port 8000

serve-static:  ## Serve web/ alone (no engine) at http://localhost:8001
	$(RUN) python -m http.server 8001 --bind 127.0.0.1 --directory web

all: setup data verify app  ## setup -> data -> verify -> app

clean-clone:  ## Copy published files to a temp folder and run setup + verify there
	$(RUN) python scripts/clean_clone_check.py

.PHONY: docs
docs:  ## Fail if a generated document table is stale
	$(RUN) python scripts/gen_docs_tables.py --check
