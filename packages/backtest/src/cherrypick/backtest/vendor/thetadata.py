"""ThetaData, through its Python library (`transport: python`): straight to the vendor, no terminal.

The only module in the suite that imports `thetadata` or reaches the vendor's servers. The key comes
from the keyring (`credential.read_key`) and is handed to the client explicitly, so the library never
reads it from the environment or a `.env` file of its own accord.

Refusals map to stable reasons: `permission_denied` (the subscription does not cover it),
`no_data` (nothing for that request), `vendor_error` (anything else). Transient pushback (rate or
availability) is retried with backoff before it becomes a refusal.
"""

from __future__ import annotations

import math
import time
from datetime import date, timedelta

from .. import credential
from . import VendorRefusal

RETRY_DELAYS = (2.0, 5.0, 15.0)
#: The vendor refuses an end-of-day range over this many days ("max 365 days allowed").
MAX_RANGE_DAYS = 365
_TRANSIENT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "429", "OS_LIMIT", "SERVER_STARTING")
#: The vendor drops a request it has not answered in 60 s with this, every time, for sub-minute
#: history before 2026 (measured 2026-10-17: 15 s, 30 s and tick, even ten minutes of two strikes).
#: Not transient: retrying spends minutes per session for the same answer.
STREAM_REMOVED = "Stream removed"


#: The longest any one vendor call may take. A day of 1-minute greeks takes about ten seconds and a
#: day at 15 s about two minutes, so this only ever ends a call that has stopped. Some calls do
#: stop (2026-10-17): the same request, re-sent on a fresh channel, then answers in seconds.
CALL_DEADLINE_SECONDS = 180.0


class _DeadlineStub:
    """The library's gRPC stub with a deadline on every call (`timeout=` is gRPC's own argument)."""

    def __init__(self, stub, timeout: float):
        self._stub, self._timeout = stub, timeout

    def __getattr__(self, name):
        method = getattr(self._stub, name)
        if not callable(method):
            return method

        def with_deadline(*args, **kwargs):
            kwargs.setdefault("timeout", self._timeout)
            return method(*args, **kwargs)

        return with_deadline


def _num(value) -> float | None:
    """A finite float, or None (NaN, infinity and absent values alike)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _classify(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    if "UNAUTHENTICATED" in text:
        # One session per account: another client logging in with the same key ends this one.
        return "unauthenticated", text[:240]
    if "PERMISSION_DENIED" in text or "requiring a" in text:
        i = text.find("details")
        return "permission_denied", (text[i : i + 240] if i >= 0 else text[:240])
    if "NoData" in type(exc).__name__ or "No data found" in text:
        return "no_data", text[:240]
    if STREAM_REMOVED in text:
        return (
            "stream_removed",
            "the vendor dropped the request at 60 s (sub-minute history is not served for this date)",
        )
    return "vendor_error", f"{type(exc).__name__}: {text[:240]}"


def _transient(exc: Exception) -> bool:
    text = str(exc)
    return STREAM_REMOVED not in text and "UNAUTHENTICATED" not in text and any(t in text for t in _TRANSIENT)


class ThetaDataClient:
    name = "thetadata"

    def __init__(self, settings: dict):
        vendor = settings["vendor"]
        if vendor["transport"] != "python":
            raise VendorRefusal("transport_not_built", "only transport 'python' is implemented")
        key = credential.read_key(settings)
        if not key:
            raise VendorRefusal("no_key", credential.PORTAL_HINT["thetadata"])
        try:
            from thetadata import ThetaClient
        except ImportError as exc:
            raise VendorRefusal(
                "library_missing", f'{exc}; install it with pip install -e "packages/backtest[thetadata]"'
            ) from None

        self._client_class = ThetaClient
        self._client = ThetaClient(api_key=key, dataframe_type="pandas")
        # The library sets no deadline on its calls, and a stalled reply waits forever (2026-10-17:
        # both workers of a history fetch sat on one for over ten minutes). Every call goes out
        # with one, which also cancels the request at the vendor's end.
        self._client.stub = _DeadlineStub(self._client.stub, CALL_DEADLINE_SECONDS)
        #: Called with a line for every retry, so a stall is visible in the fetch's log.
        self.on_retry = lambda _line: None

    def _reconnect(self) -> None:
        """A fresh channel on the same authenticated session (the library's own
        `existing_authorized_client`), so no second login ends this one."""
        fresh = self._client_class(existing_authorized_client=self._client, dataframe_type="pandas")
        fresh.stub = _DeadlineStub(fresh.stub, CALL_DEADLINE_SECONDS)
        self._client = fresh

    def _call(self, method: str, **kw):
        """`method` on the current client (looked up per attempt, so a retry after a reconnect
        goes out on the fresh channel), retried on transient pushback."""
        for delay in (*RETRY_DELAYS, None):
            try:
                return getattr(self._client, method)(**kw)
            except Exception as exc:  # noqa: BLE001 -- the vendor library raises grpc and its own types
                if delay is not None and _transient(exc):
                    stalled = "DEADLINE_EXCEEDED" in str(exc)
                    why = "stalled; reconnecting" if stalled else "transient"
                    self.on_retry(f"retry {method} in {delay:g}s ({why})")
                    if stalled:
                        self._reconnect()
                    time.sleep(delay)
                    continue
                reason, detail = _classify(exc)
                raise VendorRefusal(reason, detail) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def expirations(self, root: str) -> list[str]:
        df = self._call("option_list_expirations", symbol=root)
        return sorted(str(x)[:10] for x in df["expiration"])

    def greeks(
        self, root: str, day: str, *, interval: str, strike_range: int, start_time: str, end_time: str
    ):
        d = date.fromisoformat(day)
        df = self._call(
            "option_history_greeks_first_order",
            symbol=root,
            expiration=d,
            date=d,
            interval=interval,
            strike_range=strike_range,
            start_time=start_time,
            end_time=end_time,
        )
        # Missing values arrive as NaN (2022 sessions carry NaN bids), and NaN passes every
        # comparison a quote check makes; they leave here as None, which every check refuses.
        out = []
        for r in df.itertuples(index=False):
            out.append(
                {
                    "strike": float(r.strike),
                    "right": str(r.right).upper(),
                    "ts": r.timestamp.timestamp(),
                    "bid": _num(r.bid),
                    "ask": _num(r.ask),
                    "delta": _num(r.delta),
                    "iv": _num(r.implied_vol),
                    "underlying": _num(r.underlying_price),
                }
            )
        return out

    def open_interest(self, root: str, day: str) -> dict:
        d = date.fromisoformat(day)
        df = self._call("option_history_open_interest", symbol=root, expiration=d, date=d)
        return {
            (str(r.right).upper(), float(r.strike)): int(r.open_interest) for r in df.itertuples(index=False)
        }

    def open_interest_all(self, root: str, day: str) -> dict:
        """Every expiry's open interest as the vendor reports it on `day`'s morning (the previous
        session's closing figure): {(expiration, right, strike): contracts}."""
        df = self._call(
            "option_history_open_interest", symbol=root, expiration="*", date=date.fromisoformat(day)
        )
        return {
            (str(r.expiration)[:10], str(r.right).upper(), float(r.strike)): int(r.open_interest)
            for r in df.itertuples(index=False)
        }

    def trades(self, root: str, day: str) -> list[dict]:
        """Every option print of `root` on `day`, every expiry, regular hours, each with the quote
        standing when it printed: {expiration, strike, right, ts_ms, size, price, bid, ask,
        condition}. A whole liquid name's day is one request of seconds (2026-10-17)."""
        df = self._call(
            "option_history_trade_quote",
            symbol=root,
            expiration="*",
            date=date.fromisoformat(day),
            start_time="09:30:00",
            end_time="16:00:00",
        )
        return [
            {
                "expiration": str(r.expiration)[:10],
                "strike": float(r.strike),
                "right": str(r.right).upper(),
                "ts_ms": int(r.trade_timestamp.timestamp() * 1000),
                "size": int(r.size),
                "price": _num(r.price),
                "bid": _num(r.bid),
                "ask": _num(r.ask),
                "condition": int(r.condition),
            }
            for r in df.itertuples(index=False)
        ]

    def index_closes(self, symbol: str, start: str, end: str) -> dict:
        """Closes in [start, end], asked for in windows under the vendor's 365-day limit. A window
        the account's index tier refuses is skipped, not fatal: the caller falls back per session."""
        out = {}
        lo, last = date.fromisoformat(start), date.fromisoformat(end)
        while lo <= last:
            hi = min(lo + timedelta(days=MAX_RANGE_DAYS - 1), last)
            try:
                df = self._call("index_history_eod", symbol=symbol, start_date=lo, end_date=hi)
            except VendorRefusal as refusal:
                if refusal.reason not in ("permission_denied", "no_data"):
                    raise
                lo = hi + timedelta(days=1)
                continue
            for r in df.itertuples(index=False):
                stamp = getattr(r, "last_trade", None) or getattr(r, "created", None)
                if stamp is not None and float(r.close) > 0:
                    out[stamp.date().isoformat()] = float(r.close)
            lo = hi + timedelta(days=1)
        return out
