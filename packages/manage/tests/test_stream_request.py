import json

from cherrypick.manage import stream_request


def _result():
    return {
        "accounts": [
            {
                "portfolio": {"benchmark": "SPY"},
                "trades": [
                    {
                        "underlying": "GLW",
                        "legs": [
                            {"symbol": "GLW   261120C00155000", "right": "C"},
                            {"symbol": "GLW   261120P00112500", "right": "P"},
                        ],
                    },
                    {"underlying": "KO", "legs": [{"symbol": "KO", "right": None}]},
                ],
            }
        ]
    }


def test_declares_every_leg_underlying_and_the_benchmark():
    assert stream_request.held_symbols(_result()) == [
        ".GLW261120C155",
        ".GLW261120P112.5",
        "GLW",
        "KO",
        "SPY",
    ]


def test_writes_legs_only_never_a_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path))
    path = stream_request.write(_result())
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "manage.json"
    assert doc["symbols"] == []
    assert ".GLW261120C155" in doc["legs"]


def test_a_failed_write_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(stream_request._sr, "write_request", boom)
    stream_request.register(_result())
