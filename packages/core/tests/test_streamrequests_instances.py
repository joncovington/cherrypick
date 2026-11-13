"""Producer instances in the stream-request registry: main reads the flat folder as always, another
instance its own subfolder, and nothing written for one is read by the other."""

from __future__ import annotations

import pytest

from cherrypick.core import streamcache
from cherrypick.core import streamrequests as sr


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CHERRYPICK_HOME", str(tmp_path / "home"))
    yield
    sr.set_instance(None)


def test_main_keeps_the_flat_folder_and_an_instance_gets_a_subfolder():
    base = sr.requests_dir()
    assert base.name == "stream_requests"
    assert sr.requests_dir("main") == base
    assert sr.requests_dir("margin") == base / "margin"


def test_requests_never_cross_between_instances():
    sr.write_request("manage", ["SPY"])
    sr.write_request("manage", ["QQQ"], instance_name="margin")
    assert sr.union_symbols() == ["SPY"]
    with sr.instance("margin"):
        assert sr.union_symbols() == ["QQQ"]
    assert sr.union_symbols() == ["SPY"]  # restored after the block


def test_the_context_restores_the_instance_after_a_raise():
    with pytest.raises(RuntimeError), sr.instance("margin"):
        raise RuntimeError("boom")
    assert sr.active_instance() == "main"


def test_a_process_instance_reads_its_own_folder():
    sr.write_request("manage", ["IWM"], instance_name="margin")
    sr.set_instance("margin")
    assert sr.union_symbols() == ["IWM"]


@pytest.mark.parametrize("bad", ["../x", "Bad", "a b"])
def test_a_bad_instance_name_is_refused(bad):
    with pytest.raises(ValueError):
        sr.requests_dir(bad)


def test_each_instance_has_its_own_cache_file():
    assert streamcache.instance_cache_path(None) == streamcache.cache_path()
    assert streamcache.instance_cache_path("main") == streamcache.cache_path()
    other = streamcache.instance_cache_path("margin")
    assert other != streamcache.cache_path() and other.parent.name == "margin"
