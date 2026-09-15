"""Hosting support: DATABASE_URL, the HTTPS proxy setting, init_db, render.yaml."""
import os
import re
import subprocess
import sys

from portfolio_tracker import config, init_db
from portfolio_tracker import db as db_module

ROOT = os.path.dirname(os.path.dirname(__file__))


def run_python(code, **env):
    """Run code in a fresh interpreter, so settings are read at import."""
    environment = dict(os.environ, FLASK_SECRET_KEY="k" * 64, FLASK_DEBUG="0")
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


class TestDatabaseUrl:
    def test_a_url_is_passed_to_the_driver_whole(self, monkeypatch):
        calls = []
        monkeypatch.setattr(db_module.psycopg2, "connect", lambda *a, **kw: calls.append((a, kw)))
        url = "postgresql://u:p@ep-x-pooler.neon.tech/neondb?sslmode=require"
        monkeypatch.setattr(config, "DATABASE_URL", url)
        db_module.get_connection()
        assert calls == [((url,), {})]

    def test_without_one_the_db_settings_are_used(self, monkeypatch):
        calls = []
        monkeypatch.setattr(db_module.psycopg2, "connect", lambda *a, **kw: calls.append((a, kw)))
        monkeypatch.setattr(config, "DATABASE_URL", None)
        db_module.get_connection()
        (args, kwargs), = calls
        assert args == ()
        assert kwargs["database"] == config.DB_NAME
        assert kwargs["host"] == config.DB_HOST

    def test_blank_counts_as_unset(self):
        # An explicit empty value also stops .env from filling it in.
        result = run_python(
            "from portfolio_tracker import config; print(repr(config.DATABASE_URL))",
            DATABASE_URL="   ",
        )
        assert result.stdout.strip() == "None", result.stderr

    def test_tests_never_see_a_configured_url(self):
        assert config.DATABASE_URL is None


PROBE = """
from flask import request, session, url_for
import web

@web.app.route("/_probe")
def probe():
    session["x"] = 1
    return url_for("index", _external=True) + " " + request.remote_addr

client = web.app.test_client()
response = client.get(
    "/_probe",
    headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "demo.onrender.com",
             "X-Forwarded-For": "203.0.113.7"},
    environ_base={"REMOTE_ADDR": "10.0.0.1"},
)
print(response.get_data(as_text=True))
print("SECURE" if "Secure" in response.headers.get("Set-Cookie", "") else "NOT-SECURE")
"""


class TestBehindHttpsProxy:
    def test_forwarded_headers_are_trusted_when_set(self):
        result = run_python(PROBE, BEHIND_HTTPS_PROXY="1")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.split()
        assert lines[0] == "https://demo.onrender.com/"
        assert lines[1] == "203.0.113.7"
        assert lines[2] == "SECURE"

    def test_forwarded_headers_are_ignored_by_default(self):
        result = run_python(PROBE, BEHIND_HTTPS_PROXY="")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.split()
        assert lines[0] == "http://localhost/"
        assert lines[1] == "10.0.0.1"
        assert lines[2] == "NOT-SECURE"


class TestInitDb:
    def test_applies_the_schema_and_is_safe_to_repeat(self, db):
        with db_module.cursor(commit=True) as cur:
            cur.execute("DROP TABLE assistant_requests")
        assert init_db.main() == 0
        assert init_db.main() == 0
        with db_module.cursor(commit=True) as cur:
            cur.execute("SELECT to_regclass('assistant_requests') IS NOT NULL")
            assert cur.fetchone()[0]

    def test_a_failure_is_reported_not_raised(self, monkeypatch, capsys):
        def refuse():
            raise RuntimeError("no route to host")
        monkeypatch.setattr(init_db, "apply_schema", refuse)
        assert init_db.main() == 1
        assert "no route to host" in capsys.readouterr().err


class TestRenderBlueprint:
    def blueprint(self):
        with open(os.path.join(ROOT, "render.yaml"), encoding="utf-8") as fh:
            return fh.read()

    def test_python_version_matches_ci(self):
        with open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as fh:
            ci = re.search(r'python-version: "([\d.]+)"', fh.read()).group(1)
        assert re.search(rf"key: PYTHON_VERSION\s*\n\s*value: {re.escape(ci)}\b", self.blueprint())

    def test_serves_with_gunicorn_on_the_given_port(self):
        text = self.blueprint()
        assert "gunicorn web:app --bind 0.0.0.0:$PORT" in text
        assert "python -m portfolio_tracker.init_db" in text

    def test_proxy_setting_is_on(self):
        assert re.search(r'key: BEHIND_HTTPS_PROXY\s*\n\s*value: "1"', self.blueprint())

    def test_secrets_are_never_written_in_the_file(self):
        text = self.blueprint()
        for key in ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GEMINI_API_KEY"):
            assert re.search(rf"key: {key}\s*\n\s*sync: false", text), key
        assert re.search(r"key: FLASK_SECRET_KEY\s*\n\s*generateValue: true", text)

    def test_gunicorn_is_pinned(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as fh:
            assert re.search(r"^gunicorn==[\d.]+$", fh.read(), re.M)
