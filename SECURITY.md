# Security policy

## Reporting a problem

Please report security problems privately through GitHub's
**Report a vulnerability** button on the repository's Security tab
(private vulnerability reporting). Do not open a public issue for a security problem.

Include what you found, where (file and line, or the steps to reproduce it), and
what you think the impact is. We aim to reply within five working days.

## Supported versions

Only the latest commit on the `main` branch is supported.

## What the project does to stay safe

- **No secrets in the repository.** Keys are optional and live only in a local,
  git-ignored `.env` file. `.env.example` lists the names with empty values.
  `scripts/secret_scan.py` runs in `make verify` and in CI and fails on key-shaped
  strings, private keys, high-entropy tokens and home-folder paths.
- **Permissive dependencies only.** `scripts/licence_audit.py` checks the licence
  of every locked package and fails on copyleft, non-commercial or unknown licences.
- **Pinned dependencies.** Python packages are pinned in `uv.lock`; Dependabot
  proposes updates weekly for Python packages, browser libraries and CI actions.
- **Code scanning.** CodeQL scans the Python and JavaScript code on every push and
  pull request to `main`, and weekly.
- **Security lint rules.** `ruff` runs with its security rules (`S`) in `make lint`.
- **Least privilege in CI.** Workflows request read-only access except where code
  scanning must upload results.
- **No data in the repository.** All data is fetched by script from its official
  public source; nothing personal is collected or stored.
