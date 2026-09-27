"""/healthz is public, so it says the app is up and nothing more.

It used to report the deployed commit and branch, whether it sat behind
a proxy, and whether market data and the assistant were configured.
Each of those tells an attacker something about the deployment.
"""
import pytest


@pytest.fixture
def anonymous(db, monkeypatch):
    """A signed-out client on a server that looks deployed: Render sets
    these variables, so a leak of either would show up here."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "3161d780f414deadbeef")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    import web as web_module

    web_module.app.config.update(TESTING=True)
    with web_module.app.test_client() as c:
        yield c


def test_the_body_is_exactly_status_ok(anonymous):
    response = anonymous.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


@pytest.mark.parametrize("word", [
    "commit", "branch", "behind_proxy", "proxy",
    "market_data", "market", "assistant", "configured",
    "3161d78", "main",
])
def test_no_deployment_detail_appears(anonymous, word):
    response = anonymous.get("/healthz")
    headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
    assert word not in response.get_data(as_text=True).lower()
    assert word not in headers.lower()
