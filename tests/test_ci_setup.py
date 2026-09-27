"""The CI configuration: database tests must run there, not skip."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "tests.yml")
PROBE = "tests/test_db.py::TestCursor::test_read_without_commit"


def run_probe(**env):
    """Run one database test in a fresh pytest, pointed at a closed port."""
    environment = dict(os.environ, DB_HOST="127.0.0.1", DB_PORT="1")
    environment.pop("REQUIRE_DB", None)      # only what the test passes
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", PROBE],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


class TestRequireDb:
    def test_without_it_an_unreachable_database_skips(self):
        result = run_probe()
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 skipped" in result.stdout

    def test_with_it_an_unreachable_database_fails(self):
        result = run_probe(REQUIRE_DB="1")
        assert result.returncode != 0
        assert "REQUIRE_DB is set but PostgreSQL is not reachable" in result.stdout


class TestWorkflow:
    def workflow(self):
        with open(WORKFLOW, encoding="utf-8") as fh:
            return fh.read()

    def test_runs_on_pushes_and_pull_requests_to_main(self):
        text = self.workflow()
        assert re.search(r"push:\s*\n\s*branches: \[main\]", text)
        assert re.search(r"pull_request:\s*\n\s*branches: \[main\]", text)

    def test_uses_a_postgres_service_and_requires_it(self):
        text = self.workflow()
        assert re.search(r"services:\s*\n\s*postgres:\s*\n\s*image: postgres:\d+", text)
        assert 'REQUIRE_DB: "1"' in text
        assert "--health-cmd" in text

    def test_pins_the_local_python_version(self):
        assert 'python-version: "3.13.15"' in self.workflow()

    def test_audits_the_dependencies_for_known_vulnerabilities(self):
        text = self.workflow()
        assert "python -m pip_audit -r requirements-dev.txt" in text
        with open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8") as fh:
            assert re.search(r"^pip-audit==[\d.]+$", fh.read(), re.M)

    def test_scans_the_app_code_with_bandit(self):
        text = self.workflow()
        assert "python -m bandit -r portfolio_tracker web.py main.py" in text
        with open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8") as fh:
            assert re.search(r"^bandit==[\d.]+$", fh.read(), re.M)

    def test_installs_the_dev_requirements_and_runs_pytest(self):
        text = self.workflow()
        assert "pip install -r requirements-dev.txt" in text
        assert "python -m pytest" in text

    def test_the_readme_badge_points_at_this_workflow(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        first_lines = "\n".join(readme.splitlines()[:5])
        assert "actions/workflows/tests.yml/badge.svg" in first_lines


class TestDependabot:
    def config(self):
        with open(os.path.join(ROOT, ".github", "dependabot.yml"), encoding="utf-8") as fh:
            return fh.read()

    def test_updates_pip_and_github_actions_weekly(self):
        text = self.config()
        for ecosystem in ("pip", "github-actions"):
            block = re.search(
                rf"package-ecosystem: {ecosystem}\n(.*?)(?=\n\s*- package-ecosystem|\Z)",
                text.replace("\r\n", "\n"), re.S)
            assert block, ecosystem
            assert "interval: weekly" in block.group(1), ecosystem
