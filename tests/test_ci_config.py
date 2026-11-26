"""CI configuration: the workflows and Dependabot cover every required check."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name):
    return yaml.safe_load((ROOT / ".github" / name).read_text(encoding="utf-8"))


def triggers(workflow):
    # PyYAML reads the bare key `on` as the boolean True
    return workflow.get("on", workflow.get(True))


def run_lines(job):
    return [step.get("run", "") for step in job["steps"]]


def all_runs(workflow):
    return "\n".join(r for job in workflow["jobs"].values() for r in run_lines(job))


@pytest.fixture(scope="module")
def ci():
    return load("workflows/ci.yml")


def test_ci_runs_on_push_and_pull_request(ci):
    on = triggers(ci)
    assert "push" in on and "pull_request" in on
    assert ci["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    "command",
    [
        "uv sync --locked",
        "make lint",
        "make licence",
        "make secrets",
        "make docs",
        "make sbom-check",
        "make test-unit",
        "make test-browser",
        "npm ci",
        "npm audit --audit-level=high",
        "npm run build",
        "npm run build:static",
        "playwright install --with-deps chromium",
    ],
)
def test_ci_has_required_step(ci, command):
    assert command in all_runs(ci)


def test_unit_and_browser_targets_cover_make_test():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'pytest -m "not realdata" --ignore=tests/e2e' in makefile
    assert 'pytest -m "not realdata" tests/e2e' in makefile
    assert "test:  ## " in makefile and '$(RUN) pytest -m "not realdata"\n' in makefile


def test_verify_runs_docs_and_sbom_checks():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    verify = next(line for line in makefile.splitlines() if line.startswith("verify:"))
    prereqs = verify.split("##")[0].split(":", 1)[1].split()
    assert {"lint", "test", "licence", "secrets", "docs", "sbom-check"} <= set(prereqs)
    assert "gen_docs_tables.py --check" in makefile and "sbom.py --check" in makefile


def test_tests_job_needs_no_optional_extras(ci):
    runs = "\n".join(run_lines(ci["jobs"]["tests"]))
    assert "uv sync --locked" in runs and "--all-extras" not in runs and "--extra" not in runs


def test_browser_job_installs_its_browser_before_testing(ci):
    runs = run_lines(ci["jobs"]["e2e"])
    install = next(i for i, r in enumerate(runs) if "playwright install" in r)
    npm = next(i for i, r in enumerate(runs) if r.strip() == "npm ci")
    test = next(i for i, r in enumerate(runs) if "make test-browser" in r)
    assert install < test and npm < test


def test_actions_are_pinned():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    ref = step["uses"].split("@", 1)
                    assert len(ref) == 2 and ref[1] not in {"main", "master", "latest"}, path


def test_no_workflow_reads_secrets():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        assert "secrets." not in path.read_text(encoding="utf-8"), path


def test_codeql_covers_python_and_javascript():
    codeql = load("workflows/codeql.yml")
    languages = codeql["jobs"]["analyze"]["strategy"]["matrix"]["language"]
    assert "python" in languages and "javascript-typescript" in languages
    assert "pull_request" in triggers(codeql) and "schedule" in triggers(codeql)


def test_dependabot_covers_every_ecosystem():
    config = load("dependabot.yml")
    seen = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert ("uv", "/") in seen or ("pip", "/") in seen
    assert ("npm", "/web") in seen
    assert ("github-actions", "/") in seen
