import pytest
from fastapi.testclient import TestClient

from cherrypick.scout import config as _config
from cherrypick.scout.app import create_app

PORT = 5057


@pytest.fixture()
def app_and_client(managed_home):
    cfg = _config.load()
    cfg["serve"]["port"] = PORT
    app = create_app(cfg)
    with TestClient(app) as client:
        yield app, client


def _headers(app, **extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_partial_builder_renders_the_shell_with_the_symbol(app_and_client):
    app, client = app_and_client
    resp = client.get("/partial/builder/aapl", headers=_headers(app))
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'data-symbol="AAPL"' in resp.text
    assert 'id="builder-svg"' in resp.text
