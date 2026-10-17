"""The config is strict: an unknown key or a bad value is refused with its path, never defaulted."""

from __future__ import annotations

import json

import pytest

from cherrypick.backtest import settings


def test_the_shipped_example_resolves_and_is_disabled():
    s = settings.load(settings.PKG_ROOT / "config.example.json")
    assert s["enabled"] is False
    assert s["vendor"]["credential"]["keyring_service"] == "cherrypick-thetadata"


def test_the_example_names_every_default_key():
    """The example is the operator's map of the config: a default it does not show is a setting
    nobody can find. Comments aside, its keys are exactly the defaults'."""
    example = json.loads((settings.PKG_ROOT / "config.example.json").read_text(encoding="utf-8"))

    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            if k.startswith("_"):
                continue
            out.add(prefix + k)
            if isinstance(v, dict):
                out |= keys(v, prefix + k + ".")
        return out

    assert keys(example) == keys(settings.DEFAULTS)


def test_without_any_config_the_example_is_used(managed_home):
    assert settings.config_file_present() is False
    assert settings.load()["enabled"] is False


def test_an_operator_config_overlays_the_defaults(managed_home):
    (managed_home / "config").mkdir(parents=True)
    (managed_home / "config" / "backtest.json").write_text(
        json.dumps({"enabled": True, "vendor": {"max_concurrent_requests": 2}}), encoding="utf-8"
    )
    s = settings.load()
    assert settings.config_file_present() is True
    assert s["enabled"] is True
    assert s["vendor"]["max_concurrent_requests"] == 2
    assert s["vendor"]["transport"] == "python", "an unset key keeps its default"


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"window": {}}, "unknown key `window`"),
        ({"windows": {"holdot": ["2025-07-01", "2026-10-12"]}}, "unknown key `windows.holdot`"),
        ({"vendor": {"credential": {"keyring": "x"}}}, "unknown key `vendor.credential.keyring`"),
        ({"vendor": {"license": {"terminated": "2026-11-01"}}}, "unknown key `vendor.license.terminated`"),
        ({"datasets": [{"symbol": "SPX", "expiry": 0}]}, "datasets[0]"),
        ({"vendor": {"name": "polygon"}}, "vendor.name"),
        ({"vendor": {"transport": "rest"}}, "vendor.transport"),
        ({"vendor": {"max_concurrent_requests": 0}}, "max_concurrent_requests"),
        ({"enabled": "yes"}, "enabled"),
        ({"windows": {"design": ["2025-06-30", "2022-05-16"]}}, "windows.design"),
        ({"windows": {"design": ["2022-05-16", "2025-08-01"]}}, "holdout must start after"),
        ({"vendor": "thetadata"}, "`vendor` must be an object"),
    ],
)
def test_bad_config_is_refused_with_its_path(raw, fragment):
    with pytest.raises(settings.SettingsError, match=None) as err:
        settings.resolve(raw)
    assert fragment in str(err.value)


def test_comment_keys_are_ignored_everywhere():
    s = settings.resolve({"_note": "x", "vendor": {"_comment": "y"}, "windows": {"_why": "z"}})
    assert "_note" not in s and "_comment" not in s["vendor"]
