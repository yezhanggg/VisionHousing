"""State codes from callers never steer the engine to another folder (path injection)."""

from __future__ import annotations

from pathlib import Path

import pytest

from visionhousing.engine import run
from visionhousing.engine.context import (
    PACKS_DIR,
    TESTLAND_DIR,
    load_context,
    pack_dir,
    safe_child,
    state_code,
)

BAD = ["../PA", "..", "../../etc", "/etc", "/var/x", "P/", "PA/../VT", "p a", "", "PAX", "4A"]


@pytest.mark.parametrize("value", BAD)
def test_bad_codes_are_refused_before_any_path(value):
    with pytest.raises(ValueError):
        state_code(value)
    with pytest.raises(ValueError):
        pack_dir(value)
    with pytest.raises(ValueError):
        load_context(value)
    with pytest.raises(ValueError):
        run(value, "adu", draws=10)


def test_good_codes():
    assert state_code("pa") == "PA"
    assert pack_dir("pa") == PACKS_DIR / "PA"
    assert pack_dir("TL") == TESTLAND_DIR


def test_unknown_well_formed_code_has_no_pack():
    with pytest.raises(FileNotFoundError, match="make data STATE=ZZ"):
        pack_dir("ZZ")


def test_safe_child_stays_inside(tmp_path):
    assert safe_child(tmp_path, "PA") == tmp_path / "PA"
    for part in ("../x", "/etc", "a/../../b"):
        with pytest.raises(ValueError):
            safe_child(tmp_path, part)
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        safe_child(tmp_path, "link")
    assert isinstance(safe_child(tmp_path, "a", "b"), Path)
