"""`python web.py` never runs the Werkzeug debugger in production.

The debugger executes arbitrary code for whoever can reach it, so the
local convenience of `python web.py` (debug on, throwaway signing key)
must switch off when ENVIRONMENT is production.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# Runs web.py as __main__ with Flask.run replaced, so the test sees the
# debug flag the app would start with and no server is ever started.
RUN_AS_MAIN = (
    "import flask, runpy; "
    "flask.Flask.run = lambda self, **kw: print('DEBUG=' + str(kw.get('debug'))); "
    "runpy.run_path('web.py', run_name='__main__')"
)


def run_web_py(**env):
    environment = dict(os.environ, FLASK_DEBUG="0", ALLOW_DEV_LOGIN="",
                       BEHIND_HTTPS_PROXY="", ENVIRONMENT="", FLASK_SECRET_KEY="k" * 64)
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-c", RUN_AS_MAIN],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


def test_production_runs_without_the_debugger():
    result = run_web_py(ENVIRONMENT="production")
    assert result.returncode == 0, result.stderr
    assert "DEBUG=False" in result.stdout


def test_local_runs_keep_the_debugger():
    result = run_web_py(ENVIRONMENT="development")
    assert result.returncode == 0, result.stderr
    assert "DEBUG=True" in result.stdout


def test_production_gets_no_throwaway_signing_key():
    result = run_web_py(ENVIRONMENT="production", FLASK_SECRET_KEY="")
    assert result.returncode != 0
    assert "FLASK_SECRET_KEY is not set" in result.stderr


def test_local_runs_still_get_a_throwaway_key():
    result = run_web_py(ENVIRONMENT="development", FLASK_SECRET_KEY="")
    assert result.returncode == 0, result.stderr
