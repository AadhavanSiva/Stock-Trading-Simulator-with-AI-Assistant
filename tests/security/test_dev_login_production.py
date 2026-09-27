"""ENVIRONMENT=production refuses dev login on its own.

The older guard keys on BEHIND_HTTPS_PROXY, which says how requests reach
the server rather than what the server is for. ENVIRONMENT=production is
the direct statement, so it refuses dev login even with the proxy setting
missing, and the proxy guard still stands beside it.
"""
import os
import re
import subprocess
import sys

import pytest

from portfolio_tracker import config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def start_app(**env):
    """Import the web app in a fresh interpreter, as a real server would.

    Every relevant setting is given explicitly, even when empty, so
    python-dotenv cannot fill it in from a developer's .env.
    """
    environment = dict(os.environ, FLASK_SECRET_KEY="k" * 64, FLASK_DEBUG="0",
                       ALLOW_DEV_LOGIN="", BEHIND_HTTPS_PROXY="", ENVIRONMENT="")
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import web"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


class TestStartup:
    def test_dev_login_in_production_fails_without_the_proxy_setting(self):
        result = start_app(ENVIRONMENT="production", ALLOW_DEV_LOGIN="1")
        assert result.returncode != 0
        assert "ConfigurationError" in result.stderr
        assert "ENVIRONMENT is production" in result.stderr

    def test_the_environment_name_is_not_case_sensitive(self):
        result = start_app(ENVIRONMENT=" Production ", ALLOW_DEV_LOGIN="true")
        assert result.returncode != 0

    def test_dev_login_in_production_behind_the_proxy_fails(self):
        result = start_app(ENVIRONMENT="production", ALLOW_DEV_LOGIN="1",
                           BEHIND_HTTPS_PROXY="1")
        assert result.returncode != 0

    def test_the_proxy_guard_still_holds_outside_production(self):
        result = start_app(ALLOW_DEV_LOGIN="1", BEHIND_HTTPS_PROXY="1")
        assert result.returncode != 0
        assert "BEHIND_HTTPS_PROXY" in result.stderr

    def test_production_without_dev_login_starts(self):
        result = start_app(ENVIRONMENT="production", BEHIND_HTTPS_PROXY="1")
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize("name", ["", "development", "test"])
    def test_dev_login_starts_outside_production(self, name):
        result = start_app(ENVIRONMENT=name, ALLOW_DEV_LOGIN="1")
        assert result.returncode == 0, result.stderr


class TestCheck:
    def test_production_alone_refuses(self):
        with pytest.raises(config.ConfigurationError, match="ENVIRONMENT is production"):
            config.check_dev_login(True, False, "production")

    def test_nothing_is_refused_without_dev_login(self):
        config.check_dev_login(False, True, "production")

    def test_defaults_to_development(self):
        config.check_dev_login(True, False)


class TestRenderBlueprint:
    def test_the_blueprint_declares_production(self):
        with open(os.path.join(ROOT, "render.yaml"), encoding="utf-8") as fh:
            blueprint = fh.read()
        assert re.search(r"key: ENVIRONMENT\s*\n\s*value: production\b", blueprint)
        assert not re.search(r"key: ALLOW_DEV_LOGIN", blueprint)
