from config import push_min_interval, ws_port


def test_ws_port_defaults_to_http_port_plus_one():
    assert ws_port({"serve": {"port": 5055}}) == 5056


def test_ws_port_explicit_override():
    assert ws_port({"serve": {"port": 5055, "ws_port": 6000}}) == 6000


def test_ws_port_default_when_serve_absent():
    assert ws_port({}) == 5056  # default http port 5055 + 1


def test_push_min_interval_default_and_override():
    assert push_min_interval({}) == 1.0
    assert push_min_interval({"serve": {"push_min_interval_seconds": 0.5}}) == 0.5
