import json

import pytest
from cherrypick.core import streamrequests as _sr

from cherrypick.contango import replay, stream_request

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def test_the_replay_holds_what_the_rule_says_and_pays_per_side():
    ratio = dict(zip(DAYS, [0.9, 0.9, 0.99, 0.9], strict=True))
    risk = dict(zip(DAYS, [100.0, 110.0, 99.0, 100.0], strict=True))
    cash = dict(zip(DAYS, [100.0, 100.0, 100.0, 101.0], strict=True))
    out = replay.run(DAYS, ratio, risk, cash, {"enter_below": 0.97, "exit_at_or_above": 0.97}, cost_bps=0)
    # in risk 05->06 (+10%) and 06->07 (-10%), out to cash on 07, back to risk on 08 (cash +1%)
    assert out["navs"][-1][1] == pytest.approx(1.10 * 0.90 * 1.01)
    assert out["switches"] == 2
    costed = replay.run(DAYS, ratio, risk, cash, {"enter_below": 0.97, "exit_at_or_above": 0.97}, cost_bps=10)
    # the first buy is one side; each later switch is two (a sell and a buy) at 10 bps each
    assert costed["navs"][-1][1] == pytest.approx(out["navs"][-1][1] * (1 - 0.001) * (1 - 0.002) ** 2)


def test_the_request_declares_every_fund_as_a_quote_only_leg(config):
    config["arms"]["svix"] = {"enabled": False, "risk_symbol": "SVIX"}
    stream_request.write(config)
    payload = json.loads(_sr.request_path("contango").read_text())
    assert payload["symbols"] == []
    assert set(payload["legs"]) == {"SVXY", "SVIX", "SHV", "VIX", "VIX3M"}
