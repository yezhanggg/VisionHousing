"""Shared test settings.

The comparisons disk cache (``api.comparison_cache``) is switched off for every
test, so no test writes into ``data/cache``; the cache's own tests turn it on
in a temporary folder.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_comparisons_disk_cache(monkeypatch):
    monkeypatch.setenv("VISIONHOUSING_COMPARISONS_DIR", "none")
