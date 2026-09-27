"""Dev login must never run on a deployed server.

It lets anyone sign in as any email address, so the app refuses to start
when it is switched on alongside BEHIND_HTTPS_PROXY, which is only set in
a deployment.
"""
import os
import subprocess
import sys

import pytest

from portfolio_tracker import config

ROOT = os.path.dirname(os.path.dirname(__file__))


def start_app(**env):
    """Import the web app in a fresh interpreter, as a real server would.

    Every setting is given explicitly, even when empty, so python-dotenv
    cannot fill it in from a developer's .env.
    """
    environment = dict(os.environ, FLASK_SECRET_KEY="k" * 64, FLASK_DEBUG="0",
                       ALLOW_DEV_LOGIN="", BEHIND_HTTPS_PROXY="", ENVIRONMENT="")
    environment.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import web"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120,
    )


class TestCheck:
    def test_both_set_is_refused(self):
        with pytest.raises(config.ConfigurationError, match="ALLOW_DEV_LOGIN"):
            config.check_dev_login(True, True)

    @pytest.mark.parametrize("allow, proxy", [(True, False), (False, True), (False, False)])
    def test_anything_else_is_allowed(self, allow, proxy):
        config.check_dev_login(allow, proxy)


class TestStartup:
    def test_dev_login_behind_the_proxy_fails_fast(self):
        result = start_app(ALLOW_DEV_LOGIN="1", BEHIND_HTTPS_PROXY="1")
        assert result.returncode != 0
        assert "ConfigurationError" in result.stderr
        assert "ALLOW_DEV_LOGIN" in result.stderr

    def test_any_spelling_of_true_is_caught(self):
        result = start_app(ALLOW_DEV_LOGIN="True", BEHIND_HTTPS_PROXY="yes")
        assert result.returncode != 0

    def test_dev_login_alone_starts_locally(self):
        result = start_app(ALLOW_DEV_LOGIN="1")
        assert result.returncode == 0, result.stderr

    def test_the_proxy_alone_starts(self):
        result = start_app(BEHIND_HTTPS_PROXY="1")
        assert result.returncode == 0, result.stderr


class TestRoute:
    def test_dev_login_alone_signs_in_locally(self, anon, monkeypatch):
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", True)
        response = anon.post("/login/dev", data={"email": "a@b.com"}, follow_redirects=True)
        assert "Signed in locally" in response.get_data(as_text=True)

    def test_with_neither_set_the_route_is_404(self, anon, monkeypatch):
        monkeypatch.setattr(config, "ALLOW_DEV_LOGIN", False)
        monkeypatch.setattr(config, "BEHIND_HTTPS_PROXY", False)
        assert anon.post("/login/dev", data={"email": "a@b.com"}).status_code == 404
