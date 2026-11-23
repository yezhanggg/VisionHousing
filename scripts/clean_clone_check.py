"""Clean-clone check: prove the project works from only the files that would be published.

Why: the Challenge requires that the tool runs from the documented setup with no
hidden manual steps, and that tests pass on a clean checkout. A test that quietly
depends on an ignored file (a local cache, a private note, a secret) would pass
here on this machine but fail for everyone else. This check catches that.

What it does:
1. Lists the publishable files with ``git ls-files --cached --others
   --exclude-standard`` (read-only git).
2. Copies only those files into a new temporary folder. Symlinks are skipped.
3. Runs ``make setup verify`` there (or only ``make test`` with ``--fast``), with
   ``VH_FILE_LIST`` pointing at the copied list so the secret scan can run
   without a git repository.
4. Deletes the temporary folder (unless ``--keep``) and exits with make's status.

Usage: ``uv run python scripts/clean_clone_check.py [--fast] [--keep]``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def publishable_files(root: Path) -> list[str]:
    """List the files git would publish, relative to ``root``.

    Inside a clean-clone copy there is no git repository, so the list written by the
    outer check (named by ``VH_FILE_LIST``) is used instead.
    """
    listed = os.environ.get("VH_FILE_LIST")
    if listed:
        return sorted(p for p in Path(listed).read_text(encoding="utf-8").splitlines() if p)
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p)


def copy_files(root: Path, files: list[str], dest: Path) -> list[str]:
    """Copy the listed regular files from ``root`` to ``dest``; return what was copied."""
    copied = []
    for rel in files:
        src = root / rel
        if src.is_symlink() or not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(rel)
    return copied


def clean_env() -> dict[str, str]:
    """Return the current environment minus variables that would leak this checkout in."""
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONPATH"}}
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    return env


def main(argv: list[str] | None = None) -> int:
    """Copy the publishable files to a temporary folder and run the checks there."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fast", action="store_true", help="run only `make test`")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folder")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    work = Path(tempfile.mkdtemp(prefix="visionhousing-clean-"))
    try:
        clone = work / "repo"
        clone.mkdir()
        copied = copy_files(root, publishable_files(root), clone)
        file_list = work / "files.txt"
        file_list.write_text("\n".join(copied) + "\n", encoding="utf-8")
        env = clean_env()
        env["VH_FILE_LIST"] = str(file_list)
        targets = ["test"] if args.fast else ["setup", "verify"]
        print(f"clean clone: copied {len(copied)} files; running make {' '.join(targets)}")
        result = subprocess.run(["make", *targets], cwd=clone, env=env, check=False)
        verdict = "passed" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
        print(f"clean clone: make {' '.join(targets)} {verdict}")
        return result.returncode
    finally:
        if args.keep:
            print(f"clean clone: kept the copy in {work.name} under the system temp folder")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
