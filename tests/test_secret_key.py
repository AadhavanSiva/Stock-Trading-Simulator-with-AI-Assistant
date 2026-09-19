"""FLASK_SECRET_KEY is required outside debug mode."""
import os
import re
import subprocess
import sys

import pytest

from portfolio_tracker import config

ROOT = os.path.dirname(os.path.dirname(__file__))


class TestResolvingTheKey:
    def test_a_configured_key_is_used_in_production(self, monkeypatch):
        monkeypatch.setattr(config, "SECRET_KEY", "a-real-key")
        assert config.flask_secret_key(debug=False) == "a-real-key"

    def test_a_configured_key_is_used_in_debug_too(self, monkeypatch):
        monkeypatch.setattr(config, "SECRET_KEY", "a-real-key")
        assert config.flask_secret_key(debug=True) == "a-real-key"

    def test_missing_key_outside_debug_fails_with_instructions(self, monkeypatch):
        monkeypatch.setattr(config, "SECRET_KEY", None)
        with pytest.raises(config.ConfigurationError) as caught:
            config.flask_secret_key(debug=False)
        message = str(caught.value)
        assert "FLASK_SECRET_KEY is not set" in message
        assert 'python -c "import secrets; print(secrets.token_hex(32))"' in message

    def test_missing_key_in_debug_gets_a_throwaway_one(self, monkeypatch):
        monkeypatch.setattr(config, "SECRET_KEY", None)
        first = config.flask_secret_key(debug=True)
        assert re.fullmatch(r"[0-9a-f]{64}", first)
        assert config.flask_secret_key(debug=True) != first


def start_app(**env):
    """Import the web app in a fresh interpreter, as `flask run` would."""
    environment = dict(os.environ, **env)
    return subprocess.run(
        [sys.executable, "-c", "import web"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


class TestStartup:
    """In a real process, not a patched config: the check must run at import.

    An explicitly empty FLASK_SECRET_KEY also stops python-dotenv filling it
    in from a developer's .env, so these do not depend on that file.
    """

    def test_production_start_without_a_key_fails_fast(self):
        result = start_app(FLASK_SECRET_KEY="", FLASK_DEBUG="0")
        assert result.returncode != 0
        assert "ConfigurationError" in result.stderr
        assert "FLASK_SECRET_KEY is not set" in result.stderr

    def test_whitespace_is_not_a_key(self):
        result = start_app(FLASK_SECRET_KEY="   ", FLASK_DEBUG="0")
        assert result.returncode != 0

    def test_debug_start_without_a_key_still_runs(self):
        result = start_app(FLASK_SECRET_KEY="", FLASK_DEBUG="1")
        assert result.returncode == 0, result.stderr

    def test_production_start_with_a_key_runs(self):
        result = start_app(FLASK_SECRET_KEY="k" * 64, FLASK_DEBUG="0")
        assert result.returncode == 0, result.stderr
