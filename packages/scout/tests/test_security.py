import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cherrypick.scout.security import SecurityMiddleware

TOKEN = "test-token"
PORT = 5057


@pytest.fixture()
def client():
    app = FastAPI()
    app.add_middleware(SecurityMiddleware, port=PORT, csrf_token=TOKEN)

    @app.get("/get")
    def _get():
        return {"ok": True}

    @app.post("/post")
    def _post():
        return {"ok": True}

    return TestClient(app)


def _headers(**extra):
    headers = {"Host": f"127.0.0.1:{PORT}"}
    headers.update(extra)
    return headers


def test_get_with_valid_host_passes(client):
    resp = client.get("/get", headers=_headers())
    assert resp.status_code == 200


def test_get_with_bad_host_is_refused(client):
    resp = client.get("/get", headers=_headers(Host="evil.example.com"))
    assert resp.status_code == 403


def test_post_without_csrf_token_is_refused(client):
    resp = client.post("/post", headers=_headers(**{"Content-Type": "application/json"}), json={"a": 1})
    assert resp.status_code == 403
    assert "csrf" in resp.json()["error"].lower()


def test_post_with_wrong_csrf_token_is_refused(client):
    resp = client.post(
        "/post",
        headers=_headers(**{"Content-Type": "application/json", "X-Csrf-Token": "nope"}),
        json={"a": 1},
    )
    assert resp.status_code == 403


def test_post_with_wrong_content_type_is_refused(client):
    resp = client.post(
        "/post",
        headers=_headers(**{"Content-Type": "text/plain", "X-Csrf-Token": TOKEN}),
        content=b"{}",
    )
    assert resp.status_code == 403
    assert "content-type" in resp.json()["error"].lower()


def test_post_from_a_foreign_origin_is_refused(client):
    resp = client.post(
        "/post",
        headers=_headers(
            **{
                "Content-Type": "application/json",
                "X-Csrf-Token": TOKEN,
                "Origin": "http://evil.example.com",
            }
        ),
        json={"a": 1},
    )
    assert resp.status_code == 403
    assert "cross-origin" in resp.json()["error"].lower()


def test_post_with_valid_token_content_type_and_local_origin_succeeds(client):
    resp = client.post(
        "/post",
        headers=_headers(
            **{
                "Content-Type": "application/json",
                "X-Csrf-Token": TOKEN,
                "Origin": f"http://127.0.0.1:{PORT}",
            }
        ),
        json={"a": 1},
    )
    assert resp.status_code == 200


def test_post_with_no_origin_header_still_succeeds(client):
    """Same-origin browser fetches always carry Origin, but a non-browser client (or a same-page
    same-origin request under some browsers) may omit it -- only a *foreign* Origin is refused."""
    resp = client.post(
        "/post",
        headers=_headers(**{"Content-Type": "application/json", "X-Csrf-Token": TOKEN}),
        json={"a": 1},
    )
    assert resp.status_code == 200
