"""Vendor clients. Each is the ONLY place its library is imported and the network reached
(tests/test_fence.py). Everything else in the package works on the plain rows they return, so a run
and its tests never need the vendor, the network or a key.

The interface every client provides (rows are plain dicts, dates ISO strings, `ts` epoch seconds):

- ``expirations(root) -> list[str]``
- ``greeks(root, day, *, interval, strike_range, start_time, end_time) -> list[dict]`` with keys
  ``strike, right ("CALL"/"PUT"), ts, bid, ask, delta, iv, underlying``
- ``open_interest(root, day) -> dict[(right, strike), int]``
- ``index_closes(symbol, start, end) -> dict[day, float]``
- ``trades(root, day) -> list[dict]`` with keys ``expiration, strike, right, ts_ms, size, price,
  bid, ask, condition``: every print of every expiry, with the quote standing at it (the flow fetch)
- ``open_interest_all(root, day) -> dict[(expiration, right, strike), int]``: every expiry

A request the vendor refuses raises `VendorRefusal` with a short, stable reason.
"""

from __future__ import annotations

#: Refusals that say nothing about the session asked for, only that no further request can succeed
#: (the account's one session was taken by another login). A fetch stops on these instead of
#: recording every remaining session as refused.
FATAL = ("unauthenticated",)


class VendorRefusal(Exception):
    """The vendor said no, or there is nothing to ask with. `reason` is stable and short; `detail`
    is the vendor's own words, kept for the coverage table."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def client_for(settings: dict):
    """The configured vendor's client. Imported lazily, so nothing but a fetch loads the library."""
    name = settings["vendor"]["name"]
    if name == "thetadata":
        from .thetadata import ThetaDataClient

        return ThetaDataClient(settings)
    raise VendorRefusal("unknown_vendor", name)
