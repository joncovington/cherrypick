import pytest

from cherrypick.scout.services.session import BrokerSession


class _FakeManager:
    def __init__(self):
        self.sessions_built = 0
        self.reset_calls = 0
        self.current = None

    def get_session(self):
        if self.current is None:
            self.sessions_built += 1
            self.current = f"session-{self.sessions_built}"
        return self.current

    def reset_session(self):
        self.reset_calls += 1
        self.current = None


@pytest.mark.asyncio
async def test_call_passes_the_session_through():
    manager = _FakeManager()
    broker = BrokerSession(manager=manager, politeness_seconds=0)

    seen = []

    async def fn(session, x):
        seen.append((session, x))
        return x * 2

    result = await broker.call(fn, 5)
    assert result == 10
    assert seen == [("session-1", 5)]


@pytest.mark.asyncio
async def test_a_401_shaped_failure_resets_the_session_and_retries_once():
    manager = _FakeManager()
    broker = BrokerSession(manager=manager, politeness_seconds=0)

    calls = []

    async def fn(session, *_a):
        calls.append(session)
        if len(calls) == 1:
            raise RuntimeError("401 Unauthorized")
        return "ok"

    result = await broker.call(fn)
    assert result == "ok"
    assert calls == ["session-1", "session-2"]
    assert manager.reset_calls == 1


@pytest.mark.asyncio
async def test_a_non_auth_failure_is_not_retried():
    manager = _FakeManager()
    broker = BrokerSession(manager=manager, politeness_seconds=0)

    async def fn(_session):
        raise RuntimeError("rate limited")

    with pytest.raises(RuntimeError, match="rate limited"):
        await broker.call(fn)
    assert manager.reset_calls == 0


@pytest.mark.asyncio
async def test_politeness_spacing_delays_a_second_call(monkeypatch):
    manager = _FakeManager()
    broker = BrokerSession(manager=manager, politeness_seconds=10.0)

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("cherrypick.scout.services.session.asyncio.sleep", fake_sleep)

    async def fn(_session):
        return "ok"

    await broker.call(fn)
    await broker.call(fn)
    assert len(sleeps) == 1  # first call has nothing to wait on; the second is throttled
    assert sleeps[0] > 0
