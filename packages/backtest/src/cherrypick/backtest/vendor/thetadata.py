"""ThetaData, through its Python library (`transport: python`): straight to the vendor, no terminal.

The only module in the suite that imports `thetadata` or reaches the vendor's servers. The key comes
from the keyring (`credential.read_key`) and is handed to the client explicitly, so the library never
reads it from the environment or a `.env` file of its own accord.

Refusals map to stable reasons: `permission_denied` (the subscription does not cover it),
`no_data` (nothing for that request), `vendor_error` (anything else). Transient pushback (rate or
availability) is retried with backoff before it becomes a refusal.
"""

from __future__ import annotations

import atexit
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cherrypick.core import home as _home
from cherrypick.core import looplock

from .. import credential
from . import VendorRefusal

#: The account allows ONE session: a second login with the key ends the first, and every later call
#: of the first fails UNAUTHENTICATED (2026-10-17). So a process takes this lock before it logs in
#: and holds it until it exits; another finds it held and WAITS, asking again every
#: `SESSION_POLL_SECONDS`, rather than killing a fetch hours into its run (the user, 2026-10-17: a
#: scheduled fetch that found the account busy should still run tonight, after the other finishes).
#: The wait ends at `SESSION_WAIT_SECONDS` with `session_in_use`: a holder alive but stuck must not
#: keep every later client waiting for ever. It is `core.looplock`'s lock: a live holder is never
#: stolen from, however long it runs; a dead one's, or one whose PID the OS has since handed to
#: another process, is reclaimed. It guards every client this package builds (fetch, fetch-flow, the
#: nightly job, any script using `client_for`); the Theta Terminal or the library used by hand does
#: not take it. The file is `<home>/state/thetadata-session.lock`, holding the holder's PID, outside
#: the licensed directory so a purge never removes a live session's lock.
SESSION_LOCK = "thetadata-session.lock"
SESSION_POLL_SECONDS = 60.0
SESSION_WAIT_SECONDS = 6 * 3600.0


def session_lock_path() -> Path:
    return _home.state_dir() / SESSION_LOCK


def _holder(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def take_session(
    path: Path | None = None,
    *,
    wait_seconds: float = SESSION_WAIT_SECONDS,
    poll_seconds: float = SESSION_POLL_SECONDS,
    log=None,
) -> Path:
    """Take the account's one session for this process, waiting for another holder to finish: asked
    again every `poll_seconds`, each wait logged with the holder's PID, until `wait_seconds` have
    passed, then `session_in_use`. A process that already holds it may take it again (each process
    makes its clients one after another). `wait_seconds=0` asks once."""
    path = path or session_lock_path()
    if _holder(path) == os.getpid():
        return path
    started = time.monotonic()
    while not looplock.acquire(path, alive=looplock.pid_alive):
        waited = time.monotonic() - started
        if waited >= wait_seconds:
            raise VendorRefusal(
                "session_in_use",
                f"another ThetaData client (pid {_holder(path)}) held the account's one session for the "
                f"{waited / 60:.0f} minutes this one waited; a second login would end it.",
            )
        if log is not None:
            held_by = _holder(path)
            log(f"waiting for the ThetaData session: pid {held_by} holds it ({waited / 60:.0f} min so far)")
        time.sleep(min(poll_seconds, max(0.0, wait_seconds - waited)))
    atexit.register(release_session, path)
    return path


def release_session(path: Path | None = None) -> None:
    """Release the lock if this process holds it; never another's."""
    path = path or session_lock_path()
    if _holder(path) == os.getpid():
        looplock.release(path)


_ET = ZoneInfo("America/New_York")
RETRY_DELAYS = (2.0, 5.0, 15.0)
#: The vendor refuses an end-of-day range over this many days ("max 365 days allowed").
MAX_RANGE_DAYS = 365
#: Calendar days per request when fetching an expiry's daily path (`eod_chain_range`).
PATH_CHUNK_DAYS = 7
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


def _has_weekday(lo: date, hi: date) -> bool:
    """Whether [lo, hi] holds a Monday-to-Friday day, i.e. could have held a session."""
    return any((lo + timedelta(days=k)).weekday() < 5 for k in range(min((hi - lo).days + 1, 7)))


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

        # Before the login, never after: a refused second client must not have ended the first.
        self._session_lock = take_session(log=lambda line: print(line, file=sys.stderr, flush=True))
        self._client_class = ThetaClient
        try:
            self._client = ThetaClient(api_key=key, dataframe_type="pandas")
        except Exception:
            release_session(self._session_lock)
            raise
        # The library sets no deadline on its calls, and a stalled reply waits forever (2026-10-17:
        # both workers of a history fetch sat on one for over ten minutes). Every call goes out
        # with one, which also cancels the request at the vendor's end.
        self._client.stub = _DeadlineStub(self._client.stub, CALL_DEADLINE_SECONDS)
        #: Called with a line for every retry, so a stall is visible in the fetch's log.
        self.on_retry = lambda _line: None

    def close(self) -> None:
        """Give the session up before the process ends (it is released at exit regardless)."""
        release_session(getattr(self, "_session_lock", None))

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

    def chain_window(
        self,
        root: str,
        day: str,
        expiration: str,
        start: str,
        end: str,
        *,
        interval: str = "5m",
        strike_range: int | None = None,
    ) -> list[dict]:
        """One expiry's chain through a time window of one session: every strike and side at each
        `interval` bar from `start` to `end` ("HH:MM", ET), with the vendor's bid, ask, delta and
        implied volatility and the underlying's price (a general form of `greeks`, which is fixed to
        a 0DTE expiry of the day asked for). `strike_range` bounds the strikes either side of the
        money; None asks for the whole chain. Rows leave with ``bar`` ("HH:MM", the vendor's own
        stamp read as ET wall clock) and missing values as None, never NaN. The vendor's `no_data`
        reaches the caller as a refusal (an empty list, as an empty answer), and either is recorded
        as empty.

        The window's end bar is inclusive (checked 2026-10-30 on a 15:35-15:50 window at 5 minutes:
        four bars). Single stocks answer at 5 minutes before 2026 (checked for 2021 and 2025); how
        completely, across names and years, is the item 31 pilot's measurement. A refusal or an
        empty answer is recorded as what it is, never guessed around."""
        d = date.fromisoformat(day)
        kw = {
            "symbol": root,
            "expiration": date.fromisoformat(expiration),
            "date": d,
            "interval": interval,
            "start_time": f"{start}:00",
            "end_time": f"{end}:00",
        }
        if strike_range is not None:
            kw["strike_range"] = strike_range
        df = self._call("option_history_greeks_first_order", **kw)
        out = []
        for r in df.itertuples(index=False):
            stamp = r.timestamp
            if getattr(stamp, "tzinfo", None) is not None:
                stamp = stamp.astimezone(_ET)
            out.append(
                {
                    "bar": f"{stamp.hour:02d}:{stamp.minute:02d}",
                    "strike": float(r.strike),
                    "right": str(r.right).upper(),
                    "bid": _num(getattr(r, "bid", None)),
                    "ask": _num(getattr(r, "ask", None)),
                    "delta": _num(getattr(r, "delta", None)),
                    "iv": _num(getattr(r, "implied_vol", None)),
                    "underlying": _num(getattr(r, "underlying_price", None)),
                }
            )
        return out

    def minute_span(
        self,
        root: str,
        expiration: str,
        first: str,
        last: str,
        *,
        right: str,
        start_time: str,
        end_time: str,
        strike_range: int,
    ) -> list[dict]:
        """One expiry's minute greeks for one side over the sessions `first` through `last`, each
        session from `start_time` to `end_time` ("HH:MM:SS", ET), `strike_range` strikes either side
        of the money (the add-on weeklies fetch, `addonfetch.py`). Rows leave with ``day`` (the ET
        session), ``ts`` (epoch seconds), ``strike``, ``right`` ("P"/"C"), ``bid``, ``ask``,
        ``delta``, ``iv`` and ``underlying``, missing values as None, never NaN."""
        df = self._call(
            "option_history_greeks_first_order",
            symbol=root,
            expiration=date.fromisoformat(expiration),
            interval="1m",
            right=right,
            start_date=date.fromisoformat(first),
            end_date=date.fromisoformat(last),
            start_time=start_time,
            end_time=end_time,
            strike_range=strike_range,
        )
        out = []
        for r in df.itertuples(index=False):
            stamp = r.timestamp
            if getattr(stamp, "tzinfo", None) is not None:
                stamp = stamp.astimezone(_ET)
            out.append(
                {
                    "day": stamp.date().isoformat(),
                    "ts": int(stamp.timestamp()),
                    "strike": float(r.strike),
                    "right": right[0].upper(),
                    "bid": _num(r.bid),
                    "ask": _num(r.ask),
                    "delta": _num(r.delta),
                    "iv": _num(r.implied_vol),
                    "underlying": _num(r.underlying_price),
                }
            )
        return out

    def eod_chain(self, root: str, day: str, expiration: str) -> list[dict]:
        """One expiry's whole chain as the session closed: every strike and side, end-of-day bid and
        ask (the account's Standard tier serves single stocks from 2016). Missing values leave as
        None, never NaN."""
        d = date.fromisoformat(day)
        df = self._call(
            "option_history_eod",
            start_date=d,
            end_date=d,
            symbol=root,
            expiration=date.fromisoformat(expiration),
        )
        out = []
        for r in df.itertuples(index=False):
            out.append(
                {
                    "strike": float(r.strike),
                    "right": str(r.right).upper(),
                    "bid": _num(getattr(r, "bid", None)),
                    "ask": _num(getattr(r, "ask", None)),
                    "close": _num(getattr(r, "close", None)),
                    "volume": _num(getattr(r, "volume", None)),
                    "bid_size": _num(getattr(r, "bid_size", None)),
                    "ask_size": _num(getattr(r, "ask_size", None)),
                }
            )
        return out

    def eod_chain_range(self, root: str, start: str, through: str, expiration: str) -> list[dict]:
        """One expiry's whole chain at every session's close in [start, through] (under
        `MAX_RANGE_DAYS`), each row carrying its ``session``: the daily path a held contract is
        marked on. The session is the report's own date (``created``), never the last trade's,
        which for an untraded strike is an earlier day.

        Asked for `PATH_CHUNK_DAYS` at a time: a whole chain over two months is more than the vendor
        answers before it drops the stream at 60 s (SPY, 2026-10-23). Nothing is asked for after
        the expiry, when nothing trades. A chunk the vendor holds nothing for BEFORE any chunk has
        answered is skipped (the expiry was not listed yet); the whole range holding nothing is the
        vendor's `no_data`. A chunk holding nothing AFTER one has answered is a hole in the path:
        it is refused as `path_gap` (not `no_data`, which would record the path permanently
        empty), so the path is recorded refused and retried on a later run. A weekend-only tail is
        not a hole, since it held no session. A reply with rows but no ``created`` column is
        refused as `bad_response`: no row could be dated, and returning none would record the path
        empty for good. A single row missing its stamp is skipped."""
        lo, hi = date.fromisoformat(start), date.fromisoformat(through)
        if (hi - lo).days >= MAX_RANGE_DAYS:
            raise ValueError(f"an end-of-day range is under {MAX_RANGE_DAYS} days: {start}..{through}")
        expiry = date.fromisoformat(expiration)
        hi = min(hi, expiry)
        frames, empty = [], None
        while lo <= hi:
            top = min(lo + timedelta(days=PATH_CHUNK_DAYS - 1), hi)
            try:
                df = self._call(
                    "option_history_eod", start_date=lo, end_date=top, symbol=root, expiration=expiry
                )
            except VendorRefusal as refusal:
                if refusal.reason != "no_data":
                    raise
                if frames and _has_weekday(lo, top):
                    raise VendorRefusal(
                        "path_gap",
                        f"nothing for {lo}..{top} after earlier chunks answered ({refusal.detail})"[:240],
                    ) from None
                empty = refusal
            else:
                rows = list(df.itertuples(index=False))
                if rows and not hasattr(rows[0], "created"):
                    raise VendorRefusal("bad_response", f"rows for {lo}..{top} carry no created column")
                frames.append(rows)
            lo = top + timedelta(days=1)
        if not frames and empty is not None:
            raise empty
        out = []
        for r in (row for rows in frames for row in rows):
            stamp = getattr(r, "created", None)
            if stamp is None or stamp != stamp:  # no report date on this row (None, or pandas' NaT)
                continue
            out.append(
                {
                    "session": stamp.date().isoformat(),
                    "strike": float(r.strike),
                    "right": str(r.right).upper(),
                    "bid": _num(getattr(r, "bid", None)),
                    "ask": _num(getattr(r, "ask", None)),
                    "close": _num(getattr(r, "close", None)),
                    "volume": _num(getattr(r, "volume", None)),
                    "bid_size": _num(getattr(r, "bid_size", None)),
                    "ask_size": _num(getattr(r, "ask_size", None)),
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

    def vix_minutes(self, day: str, expiration: str) -> list[tuple[float, float]]:
        """The VIX index through `day`, one value a minute, from VIX options' underlying price: the
        account's tier refuses the index endpoints, and every VIX option expiry reports the same spot
        index (checked across expiries, and against Cboe's daily figures, 2026-10-18). (epoch, value)."""
        d = date.fromisoformat(day)
        df = self._call(
            "option_history_greeks_first_order",
            symbol="VIX",
            expiration=date.fromisoformat(expiration),
            date=d,
            interval="1m",
            strike_range=1,
            start_time="09:30:00",
            end_time="16:00:00",
        )
        seen: dict[float, float] = {}
        for r in df.itertuples(index=False):
            value = _num(r.underlying_price)
            if value is not None and value > 0:
                seen.setdefault(r.timestamp.timestamp(), value)
        return sorted(seen.items())

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
