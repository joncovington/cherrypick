"""ThetaData, through its Python library (`transport: python`): straight to the vendor, no terminal.

The only module in the suite that imports `thetadata` or reaches the vendor's servers. The key comes
from the keyring (`credential.read_key`) and is handed to the client explicitly, so the library never
reads it from the environment or a `.env` file of its own accord.

Refusals map to stable reasons: `permission_denied` (the subscription does not cover it),
`no_data` (nothing for that request), `vendor_error` (anything else). Transient pushback (rate or
availability) is retried with backoff before it becomes a refusal.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from .. import credential
from . import VendorRefusal

RETRY_DELAYS = (2.0, 5.0, 15.0)
#: The vendor refuses an end-of-day range over this many days ("max 365 days allowed").
MAX_RANGE_DAYS = 365
_TRANSIENT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "429", "OS_LIMIT", "SERVER_STARTING")


def _classify(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    if "PERMISSION_DENIED" in text or "requiring a" in text:
        i = text.find("details")
        return "permission_denied", (text[i : i + 240] if i >= 0 else text[:240])
    if "NoData" in type(exc).__name__ or "No data found" in text:
        return "no_data", text[:240]
    return "vendor_error", f"{type(exc).__name__}: {text[:240]}"


def _transient(exc: Exception) -> bool:
    text = str(exc)
    return any(t in text for t in _TRANSIENT)


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

        self._client = ThetaClient(api_key=key, dataframe_type="pandas")

    def _call(self, fn, **kw):
        for delay in (*RETRY_DELAYS, None):
            try:
                return fn(**kw)
            except Exception as exc:  # noqa: BLE001 -- the vendor library raises grpc and its own types
                if delay is not None and _transient(exc):
                    time.sleep(delay)
                    continue
                reason, detail = _classify(exc)
                raise VendorRefusal(reason, detail) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def expirations(self, root: str) -> list[str]:
        df = self._call(self._client.option_list_expirations, symbol=root)
        return sorted(str(x)[:10] for x in df["expiration"])

    def greeks(
        self, root: str, day: str, *, interval: str, strike_range: int, start_time: str, end_time: str
    ):
        d = date.fromisoformat(day)
        df = self._call(
            self._client.option_history_greeks_first_order,
            symbol=root,
            expiration=d,
            date=d,
            interval=interval,
            strike_range=strike_range,
            start_time=start_time,
            end_time=end_time,
        )
        out = []
        for r in df.itertuples(index=False):
            under = r.underlying_price
            out.append(
                {
                    "strike": float(r.strike),
                    "right": str(r.right).upper(),
                    "ts": r.timestamp.timestamp(),
                    "bid": float(r.bid),
                    "ask": float(r.ask),
                    "delta": float(r.delta),
                    "iv": float(r.implied_vol),
                    "underlying": None if under != under else float(under),  # NaN check
                }
            )
        return out

    def open_interest(self, root: str, day: str) -> dict:
        d = date.fromisoformat(day)
        df = self._call(self._client.option_history_open_interest, symbol=root, expiration=d, date=d)
        return {
            (str(r.right).upper(), float(r.strike)): int(r.open_interest) for r in df.itertuples(index=False)
        }

    def index_closes(self, symbol: str, start: str, end: str) -> dict:
        """Closes in [start, end], asked for in windows under the vendor's 365-day limit."""
        out = {}
        lo, last = date.fromisoformat(start), date.fromisoformat(end)
        while lo <= last:
            hi = min(lo + timedelta(days=MAX_RANGE_DAYS - 1), last)
            df = self._call(self._client.index_history_eod, symbol=symbol, start_date=lo, end_date=hi)
            for r in df.itertuples(index=False):
                stamp = getattr(r, "last_trade", None) or getattr(r, "created", None)
                if stamp is not None and float(r.close) > 0:
                    out[stamp.date().isoformat()] = float(r.close)
            lo = hi + timedelta(days=1)
        return out
