import json

import pytest

from push import GexPushServer, broadcast_key

CFG = {"serve": {"port": 5055, "push_min_interval_seconds": 1.0}, "symbols": ["SPX"]}


def payload(symbol="SPX", price=7480.0, net=1.0):
    return {
        "ok": True, "symbol": symbol, "underlying_price": price,
        "expiration": "2026-07-20", "source": "stream_cache",
        "series": [{"strike": 7500, "net_gex": net, "call_oi": 10, "put_oi": 5,
                    "call_vol": 2, "put_vol": 1}],
        "spot_history": [price],  # excluded from the key
    }


def test_key_ignores_spot_history_and_reacts_to_strike_change():
    a = payload()
    b = payload()
    b["spot_history"] = [1, 2, 3]
    assert broadcast_key(a) == broadcast_key(b)  # spot_history excluded
    c = payload(net=2.0)
    assert broadcast_key(a) != broadcast_key(c)  # net_gex change registers
    d = payload(price=7481.0)
    assert broadcast_key(a) != broadcast_key(d)  # underlying change registers


class FakeWS:
    def __init__(self):
        self.sent = []
        self.open = True

    async def send(self, msg):
        if not self.open:
            raise ConnectionError("closed")
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_tick_broadcasts_only_on_change():
    seq = [payload(net=1.0), payload(net=1.0), payload(net=2.0)]
    calls = {"i": 0}

    def build(cfg, symbol):
        p = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return p

    srv = GexPushServer(CFG, build=build)
    ws = FakeWS()
    srv.clients["SPX"] = {ws}
    srv._last_key["SPX"] = None

    await srv.tick()          # first build -> initial broadcast
    assert len(ws.sent) == 1
    await srv.tick()          # identical -> no send
    assert len(ws.sent) == 1
    await srv.tick()          # net_gex changed -> send
    assert len(ws.sent) == 2


@pytest.mark.asyncio
async def test_one_failed_client_does_not_block_another():
    def build(cfg, symbol):
        return payload(net=float(__import__("time").time()))  # always "changes"

    srv = GexPushServer(CFG, build=build)
    good, bad = FakeWS(), FakeWS()
    bad.open = False
    srv.clients["SPX"] = {good, bad}
    srv._last_key["SPX"] = None
    await srv.tick()
    assert len(good.sent) == 1
    assert bad not in srv.clients["SPX"]  # dropped on send failure


@pytest.mark.asyncio
async def test_snapshot_rebuilds_for_newcomer_not_stale_cache():
    state = {"net": 1.0}

    def build(cfg, symbol):
        return payload(net=state["net"])

    srv = GexPushServer(CFG, build=build)
    a = FakeWS()
    await srv._snapshot(a, "SPX")          # caches net=1.0
    assert broadcast_key(json.loads(a.sent[0])) == broadcast_key(payload(net=1.0))
    state["net"] = 2.0                       # backing data moved while unwatched
    b = FakeWS()
    await srv._snapshot(b, "SPX")          # newcomer must see net=2.0, not cached 1.0
    assert json.loads(b.sent[0])["series"][0]["net_gex"] == 2.0
