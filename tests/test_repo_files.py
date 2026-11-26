"""Repository files: workflows are well formed and every package explains itself."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in data:  # YAML 1.1 reads the bare key `on` as True
        data["on"] = data.pop(True)
    return data


def test_workflows_have_minimal_permissions_and_pinned_actions():
    for path in WORKFLOWS.glob("*.yml"):
        wf = load(path)
        assert {"name", "on", "jobs", "permissions"} <= set(wf), path.name
        assert wf["permissions"] == {"contents": "read"}, path.name
        for job in wf["jobs"].values():
            assert "runs-on" in job and job["steps"]
            for step in job["steps"]:
                if "uses" in step:
                    ref = step["uses"].split("@")[1]
                    assert ref.startswith("v") and ref[1:].isdigit(), step["uses"]


def test_ci_runs_the_checks_on_python_312():
    jobs = load(WORKFLOWS / "ci.yml")["jobs"]
    runs = [s.get("run") for job in jobs.values() for s in job["steps"]]
    assert "make lint" in runs and "make test-unit" in runs and "make test-browser" in runs
    uvs = [s for job in jobs.values() for s in job["steps"] if "setup-uv" in s.get("uses", "")]
    assert uvs and all(uv["with"]["python-version"] == "3.12" for uv in uvs)


def test_codeql_languages():
    job = load(WORKFLOWS / "codeql.yml")["jobs"]["analyze"]
    assert set(job["strategy"]["matrix"]["language"]) == {"python", "javascript-typescript"}


def test_dependabot_ecosystems():
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    pairs = {(u["package-ecosystem"], u["directory"]) for u in cfg["updates"]}
    assert pairs == {("uv", "/"), ("npm", "/web"), ("github-actions", "/")}


def test_every_package_has_a_plain_docstring():
    inits = list((ROOT / "src" / "visionhousing").rglob("__init__.py"))
    assert len(inits) >= 13
    for init in inits:
        doc = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert doc and len(doc.split()) >= 15, init


def test_licence_is_full_apache_text():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text and len(text) > 10_000


def test_env_example_has_only_empty_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, _, value = line.partition("=")
            assert value == "", name
