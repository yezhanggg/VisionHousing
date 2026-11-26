"""Secret scan: fail if a published file holds a key, a private key or a home-folder path.

Why: the Challenge forbids committed credentials. This scan runs inside
``make verify`` so a leak is caught before anything is pushed. It uses only the
standard library.

Which files: everything git would publish, that is tracked files plus untracked
files that are not ignored (``git ls-files --cached --others --exclude-standard``,
a read-only git command). Ignored files such as ``.env`` are never opened. When
there is no git repository (for example in the clean-clone copy) it reads the file
list named by the ``VH_FILE_LIST`` environment variable, or walks the folder while
skipping tool folders.

What it looks for:
- key-shaped strings from well-known services (cloud, code hosting, chat, payment);
- a value assigned to any variable named in ``.env.example`` (for example
  ``CENSUS_API_KEY``);
- private-key headers;
- long high-entropy tokens that look random;
- home-folder paths such as a macOS user folder or a Linux home folder.

A line can be excused by adding the marker ``secret-scan: allow`` to it. Use that
only for a reviewed false positive.

Usage: ``uv run python scripts/secret_scan.py [--root PATH]``. Exits 1 on findings.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_MARKER = "secret-scan: allow"
MAX_BYTES = 2_000_000

# Folders skipped when walking without git. They hold tools and caches, not sources.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "nopush",
    "no-push",
    ".claude",
    "data",
    "outputs",
    "artifacts",
    "build",
    "dist",
}
SKIP_FILES = {".env", "CLAUDE.local.md", ".DS_Store"}

# Lockfiles are full of published checksums, which look random but are not secrets.
ENTROPY_EXEMPT = {"uv.lock", "package-lock.json"}
# The static site's compact place profiles (``visionhousing.compact_profiles``) store one
# character per place as an index into a short list ("0123…xyzAB…"), which looks random but is
# engine output. Only the randomness check is skipped there; every named key pattern still runs.
ENTROPY_EXEMPT_PATH = re.compile(r"^web/public/data/[A-Z]{2}/profiles/[^/]+\.json$")

# A web address's host and path (long file names, URL slugs) look random but are public.
# Only that part is skipped by the randomness check: a query string (``?key=…``) and
# every named key pattern are still checked.
URL_PATH = re.compile(r"https?://[^\s'\"<>?#]+")

KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "cloud access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "code-hosting token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"
    ),
    "chat token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "browser API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "payment secret key": re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b"),
    "AI service key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{32,}\b"),
    "package registry token": re.compile(r"\b(?:npm_[A-Za-z0-9]{36}|pypi-[A-Za-z0-9_\-]{50,})\b"),
}
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
# Built in two pieces so this file does not match itself.
HOME_PATH = re.compile(r"(?<![\w.])/(?:" + "Users" + r"|home)/(?!<)[A-Za-z0-9][A-Za-z0-9._-]*")
# A generic home-folder placeholder is fine in documentation.
HOME_PLACEHOLDERS = {"name", "user", "username", "you", "me", "runner", "shared"}
TOKEN = re.compile(r"[A-Za-z0-9+/_\-=]{32,}")


@dataclass(frozen=True)
class Hit:
    """One finding: which file and line, what kind, and a redacted preview."""

    path: str
    line: int
    kind: str
    preview: str


def redact(text: str) -> str:
    """Show only the first four characters of a suspected secret."""
    return text[:4] + "…" + f"({len(text)} chars)"


def shannon_entropy(text: str) -> float:
    """Return the Shannon entropy of a string in bits per character."""
    if not text:
        return 0.0
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_random(token: str) -> bool:
    """Guess whether a long token is a random secret rather than a word or a path."""
    if len(token) < 32 or "--" in token or "__" in token:
        return False
    classes = sum(bool(re.search(p, token)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[+/_\-=]"))
    if re.fullmatch(r"[0-9a-fA-F]+", token):
        # Hex strings are almost always checksums or commit ids. Hex keys are caught by
        # the named-variable rule instead.
        return False
    return classes >= 3 and shannon_entropy(token) > 4.3


def env_names(root: Path) -> list[str]:
    """Read the variable names declared in ``.env.example`` (never ``.env`` itself)."""
    example = root / ".env.example"
    if not example.exists():
        return []
    names = []
    for line in example.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            names.append(m.group(1))
    return names


def scan_text(path: str, text: str, names: list[str]) -> list[Hit]:
    """Scan one file's text and return every finding."""
    hits: list[Hit] = []
    assign = (
        re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b\s*[:=]\s*['\"]?([^\s'\"#]{8,})")
        if names
        else None
    )
    check_entropy = Path(path).name not in ENTROPY_EXEMPT and not ENTROPY_EXEMPT_PATH.match(
        Path(path).as_posix()
    )
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in KEY_PATTERNS.items():
            hits.extend(Hit(path, number, kind, redact(m.group(0))) for m in pattern.finditer(line))
        if PRIVATE_KEY.search(line):
            hits.append(Hit(path, number, "private-key header", "-----BEGIN … PRIVATE KEY-----"))
        if assign:
            for m in assign.finditer(line):
                hits.append(Hit(path, number, f"value for {m.group(1)}", redact(m.group(2))))
        for m in HOME_PATH.finditer(line):
            user = m.group(0).split("/")[2]
            if user.lower() not in HOME_PLACEHOLDERS:
                hits.append(Hit(path, number, "home-folder path", m.group(0)[:7] + "…"))
        if check_entropy:
            for m in TOKEN.finditer(URL_PATH.sub(" ", line)):
                if looks_random(m.group(0)):
                    hits.append(Hit(path, number, "high-entropy token", redact(m.group(0))))
    return hits


def publishable_files(root: Path) -> list[str]:
    """List the files that would be published, relative to ``root``."""
    listed = os.environ.get("VH_FILE_LIST")
    if listed:
        return [p for p in Path(listed).read_text(encoding="utf-8").splitlines() if p]
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        return [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return list(_walk(root))


def _walk(root: Path) -> Iterator[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            if name not in SKIP_FILES and not name.startswith(".env."):
                yield str((Path(dirpath) / name).relative_to(root))
    # .env.example is the one .env-style file that is published
    if (root / ".env.example").exists():
        yield ".env.example"


def scan(root: Path) -> list[Hit]:
    """Scan every publishable file under ``root``; skip symlinks and binary files."""
    names = env_names(root)
    hits: list[Hit] = []
    for rel in sorted(set(publishable_files(root))):
        path = root / rel
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        hits.extend(scan_text(rel, data.decode("utf-8", errors="replace"), names))
    return hits


def main(argv: list[str] | None = None) -> int:
    """Run the scan from the command line; return 0 when nothing is found."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    files = publishable_files(root)
    hits = scan(root)
    for h in hits:
        print(f"{h.path}:{h.line}: {h.kind}: {h.preview}")
    print(f"secret scan: {len(set(files))} files checked, {len(hits)} findings")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
