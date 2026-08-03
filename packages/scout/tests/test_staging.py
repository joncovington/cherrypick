import pytest

from cherrypick.scout.services import cache as _cache
from cherrypick.scout.services import staging as _staging
from cherrypick.scout.services.session import BrokerSession

_LEGS = [
    {"symbol": "AAPL  260116P00150000", "quantity": -1, "price": 2.00},
    {"symbol": "AAPL  260116P00145000", "quantity": 1, "price": 1.00},
]


@pytest.fixture()
def conn(tmp_path):
    db = _cache.open_db(tmp_path / "cache.db")
    yield db
    db.close()


class _FakeManager:
    def get_session(self):
        return "session"

    def reset_session(self):
        pass


def _broker_session():
    return BrokerSession(manager=_FakeManager(), politeness_seconds=0)


class _FakeAccount:
    account_number = "5WT99998888"

    def __init__(self, *, errors=(), bpe=None):
        self.calls = []
        self._errors = list(errors)
        self._bpe = bpe

    async def place_order(self, session, order, dry_run):
        self.calls.append(dry_run)
        return _FakePreflight(self._errors, self._bpe)


class _FakeBPE:
    def __init__(self, change):
        self.current_buying_power = "1000"
        self.new_buying_power = "800"
        self.change_in_buying_power = change


class _FakePreflight:
    def __init__(self, errors, bpe):
        self.errors = errors
        self.warnings = []
        self.buying_power_effect = bpe


# --------------------------------------------------------------------------- build_order_spec


def test_build_order_spec_maps_actions_and_quantities():
    spec = _staging.build_order_spec(_LEGS)
    legs = spec["legs"]
    assert legs[0] == {
        "instrument_type": "Equity Option",
        "symbol": "AAPL  260116P00150000",
        "action": "sell to open",
        "quantity": 1,
    }
    assert legs[1]["action"] == "buy to open"


def test_build_order_spec_computes_net_credit_per_share():
    # short leg receives 2.00, long leg costs 1.00 -> net credit of 1.00/share
    spec = _staging.build_order_spec(_LEGS)
    assert spec["price"] == pytest.approx(1.00)
    assert spec["price_effect"] == "credit"


def test_build_order_spec_computes_net_debit():
    legs = [
        {"symbol": "X", "quantity": 1, "price": 2.00},
        {"symbol": "Y", "quantity": -1, "price": 1.00},
    ]
    spec = _staging.build_order_spec(legs)
    assert spec["price"] == pytest.approx(1.00)
    assert spec["price_effect"] == "debit"


def test_build_order_spec_omits_price_when_net_is_zero():
    legs = [{"symbol": "X", "quantity": 1, "price": 1.00}, {"symbol": "Y", "quantity": -1, "price": 1.00}]
    spec = _staging.build_order_spec(legs)
    assert "price" not in spec


# --------------------------------------------------------------------------- dry_run_order


@pytest.mark.asyncio
async def test_dry_run_order_masks_the_account_number(monkeypatch):
    account = _FakeAccount(bpe=_FakeBPE("-500"))

    async def fake_resolve_account(session, *a, **kw):
        return account

    monkeypatch.setattr(_staging._broker, "resolve_account", fake_resolve_account)

    result = await _staging.dry_run_order(_broker_session(), _LEGS)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["account_number"] == "****8888"
    assert account.calls == [True]  # the SDK's own dry_run kwarg -- always True, never a second call


@pytest.mark.asyncio
async def test_dry_run_order_surfaces_preflight_errors(monkeypatch):
    async def fake_resolve_account(session, *a, **kw):
        return _FakeAccount(errors=["insufficient buying power"])

    monkeypatch.setattr(_staging._broker, "resolve_account", fake_resolve_account)

    result = await _staging.dry_run_order(_broker_session(), _LEGS)
    assert result["ok"] is False
    assert "insufficient buying power" in result["problems"]


@pytest.mark.asyncio
async def test_dry_run_order_never_raises_on_a_broker_failure(monkeypatch):
    async def fake_resolve_account(session, *a, **kw):
        raise RuntimeError("no accounts found")

    monkeypatch.setattr(_staging._broker, "resolve_account", fake_resolve_account)

    result = await _staging.dry_run_order(_broker_session(), _LEGS)
    assert result["ok"] is False
    assert "no accounts found" in result["error"]


@pytest.mark.asyncio
async def test_dry_run_order_rejects_an_empty_leg_list():
    result = await _staging.dry_run_order(_broker_session(), [])
    assert result == {"ok": False, "error": "no legs to validate"}


# --------------------------------------------------------------------------- staged tickets


@pytest.mark.asyncio
async def test_stage_ticket_persists_even_when_validation_fails(conn, monkeypatch):
    async def broken_resolve_account(session, *a, **kw):
        raise RuntimeError("credentials not configured")

    monkeypatch.setattr(_staging._broker, "resolve_account", broken_resolve_account)

    ticket = await _staging.stage_ticket(
        conn,
        _broker_session(),
        symbol="AAPL",
        strategy="put_credit_spread",
        legs=_LEGS,
        credit=100.0,
        max_risk=400.0,
        note="test ticket",
        now=1000.0,
    )
    assert ticket["status"] == "staged"
    assert ticket["dry_run"]["ok"] is False
    assert ticket["credit"] == 100.0

    staged = _staging.list_staged(conn)
    assert len(staged) == 1
    assert staged[0]["id"] == ticket["id"]
    assert staged[0]["legs"] == _LEGS
    assert staged[0]["note"] == "test ticket"


@pytest.mark.asyncio
async def test_stage_ticket_records_a_successful_dry_run(conn, monkeypatch):
    account = _FakeAccount(bpe=_FakeBPE("-500"))

    async def fake_resolve_account(session, *a, **kw):
        return account

    monkeypatch.setattr(_staging._broker, "resolve_account", fake_resolve_account)

    ticket = await _staging.stage_ticket(
        conn,
        _broker_session(),
        symbol="AAPL",
        strategy="put_credit_spread",
        legs=_LEGS,
        credit=100.0,
        max_risk=400.0,
        note=None,
        now=1000.0,
    )
    assert ticket["dry_run"]["ok"] is True
    assert ticket["dry_run"]["account_number"] == "****8888"


def test_list_staged_orders_most_recent_first(conn):
    conn.execute(
        "INSERT INTO staged_orders (id, created_at, symbol, strategy, legs_json, credit, max_risk, "
        "dry_run_json, note, status) VALUES ('a', 1.0, 'AAPL', 'x', '[]', NULL, NULL, NULL, NULL, "
        "'staged')"
    )
    conn.execute(
        "INSERT INTO staged_orders (id, created_at, symbol, strategy, legs_json, credit, max_risk, "
        "dry_run_json, note, status) VALUES ('b', 2.0, 'MSFT', 'x', '[]', NULL, NULL, NULL, NULL, "
        "'staged')"
    )
    conn.commit()
    ids = [t["id"] for t in _staging.list_staged(conn)]
    assert ids == ["b", "a"]


def test_delete_staged_removes_the_row_and_reports_success(conn):
    conn.execute(
        "INSERT INTO staged_orders (id, created_at, symbol, strategy, legs_json, credit, max_risk, "
        "dry_run_json, note, status) VALUES ('a', 1.0, 'AAPL', 'x', '[]', NULL, NULL, NULL, NULL, "
        "'staged')"
    )
    conn.commit()
    assert _staging.delete_staged(conn, "a") is True
    assert _staging.list_staged(conn) == []
    assert _staging.delete_staged(conn, "a") is False  # already gone
