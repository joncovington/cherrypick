"""`scripts/pmcc_earnings_refresh.py` end to end: through the config editor, against a temp home.

Here rather than in packages/pmcc/tests because the script writes through this package's config
editor, which the pmcc test environment does not install (the pure plan is tested there)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "pmcc_earnings_refresh.py"


def _load():
    spec = importlib.util.spec_from_file_location("pmcc_earnings_refresh", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _home(tmp_path, monkeypatch) -> Path:
    """A temp home with a minimal suite config (the editor loads it) and a config directory."""
    managed_home = tmp_path / "cherrypick-home"
    managed_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHERRYPICK_HOME", str(managed_home))
    (managed_home / "config.json").write_text(json.dumps({"modules": {}}), encoding="utf-8")
    cfg_dir = managed_home / "config"
    cfg_dir.mkdir()
    return cfg_dir


def test_a_run_writes_through_the_config_editor_and_a_repeat_writes_nothing(tmp_path, monkeypatch, capsys):
    cfg_dir = _home(tmp_path, monkeypatch)
    path = cfg_dir / "pmcc.json"
    path.write_text(
        json.dumps(
            {
                "symbols": ["XSP", "AMZN"],
                "earnings": {
                    "XSP": {"kind": "etf", "declared_through": "2099-12-31", "dates": []},
                    "AMZN": {"declared_through": "2026-10-01", "dates": []},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    mod = _load()
    monkeypatch.setattr(mod, "read_calendar", lambda symbols: ("2026-11-13", {"AMZN": ["2026-10-29"]}))
    assert mod.main([]) == 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["earnings"]["AMZN"]["dates"] == ["2026-10-29"]
    assert doc["earnings"]["AMZN"]["declared_through"] == "2026-11-06"
    assert doc["earnings"]["XSP"] == {"kind": "etf", "declared_through": "2099-12-31", "dates": []}
    capsys.readouterr()
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out).get("unchanged") is True


def test_an_unreadable_calendar_writes_nothing(tmp_path, monkeypatch):
    cfg_dir = _home(tmp_path, monkeypatch)
    path = cfg_dir / "pmcc.json"
    text = json.dumps(
        {"symbols": ["AMZN"], "earnings": {"AMZN": {"declared_through": "2026-10-01", "dates": []}}}
    )
    path.write_text(text, encoding="utf-8")
    mod = _load()

    def boom(symbols):
        raise ConnectionError("dolt sql-server down")

    monkeypatch.setattr(mod, "read_calendar", boom)
    assert mod.main([]) == 1
    assert path.read_text(encoding="utf-8") == text
