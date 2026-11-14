"""The engine's data: what a decision is made from (`Snapshot`), what it chooses (`Entry`) and holds
(`Position`). Plain data, no I/O: the backtest builds a snapshot from the vendor store and the paper loop
from the stream cache, and both hand it to `engine.decide`.

Times are minutes since midnight Eastern. A quote row is a dict with `strike`, `right` ("C" or "P"),
`bid`, `ask`, and optionally `delta`, `gamma`, `iv`, `oi`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


def hhmm(text: str) -> int:
    """ "13:45" -> 825."""
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


class Chain:
    """One expiry's quotes, indexed by (strike, right)."""

    def __init__(self, expiry: str, rows: list[dict]):
        self.expiry = expiry
        self.rows = [r for r in rows if r.get("strike") is not None]
        self._by = {(float(r["strike"]), r["right"]): r for r in self.rows}
        self.strikes = sorted({k[0] for k in self._by})

    def get(self, strike: float, right: str) -> dict | None:
        return self._by.get((float(strike), right))

    def both(self, strike: float) -> bool:
        return (float(strike), "C") in self._by and (float(strike), "P") in self._by

    def nearest_strike(self, target: float, *, mode: str = "nearest") -> float | None:
        """The listed strike at `target`: `nearest` (ties go to the lower), `down` (highest at or below)
        or `up` (lowest at or above). None when no listed strike qualifies."""
        if not self.strikes:
            return None
        if mode == "down":
            c = [k for k in self.strikes if k <= target + 1e-9]
            return max(c) if c else None
        if mode == "up":
            c = [k for k in self.strikes if k >= target - 1e-9]
            return min(c) if c else None
        return min(self.strikes, key=lambda k: (abs(k - target), k))


@dataclass
class OpenPosition:
    """What the engine needs to know of a position it already holds (any arm)."""

    arm: str
    entry_session: str
    expiry: str


@dataclass
class Snapshot:
    """Everything one decision reads. `closes` are daily closes strictly before `session`, oldest
    first. `trail` is the underlying's one-minute spot from the open through `minute`, as
    (minute, spot). `gex` maps an expiry to the per-strike series `core.gex.compute_gex_profile` returns.
    `events` answers `occurs(label, first, last)`. `entries` maps an arm to its entry sessions
    (ISO dates), `open_positions` lists what is open."""

    session: str
    minute: int
    symbol: str
    spot: float
    prev_close: float
    day_open: float
    chains: dict[str, Chain]
    trail: list[tuple[int, float]]
    closes: list[float]
    vix: float | None = None
    vix_prev_close: float | None = None
    gex: dict[str, list[dict]] = field(default_factory=dict)
    events: object = None
    entries: dict[str, list[str]] = field(default_factory=dict)
    open_positions: list[OpenPosition] = field(default_factory=list)
    listed_expiries: list[str] = field(default_factory=list)
    close_minute: int = 960

    @property
    def session_date(self) -> date:
        return date.fromisoformat(self.session)

    def to_dict(self) -> dict:
        """JSON-able form (the paper loop's recorded decision input; round-trips through `from_dict`)."""
        d = asdict(self)
        d["chains"] = {k: {"expiry": c.expiry, "rows": c.rows} for k, c in self.chains.items()}
        d["events"] = None
        d["trail"] = [list(p) for p in self.trail]
        return d

    @classmethod
    def from_dict(cls, d: dict, events=None) -> Snapshot:
        d = dict(d)
        d["chains"] = {k: Chain(v["expiry"], v["rows"]) for k, v in d["chains"].items()}
        d["trail"] = [(int(m), float(s)) for m, s in d["trail"]]
        d["open_positions"] = [OpenPosition(**p) for p in d.get("open_positions", [])]
        d["events"] = events
        return cls(**d)


@dataclass
class Leg:
    name: str
    right: str
    side: int  # +1 bought, -1 sold
    strike: float


@dataclass
class Entry:
    """A chosen structure with its sizes and prices (per share, signed so a credit is +)."""

    arm: str
    session: str
    minute: int
    expiry: str
    legs: list[Leg]
    quantity: int
    prices: dict  # mid, natural, working
    max_loss: float  # per share, positive
    max_profit: float | None  # per share; None when unbounded
    spot: float
    slippage: dict  # fill kind -> dollars for the whole position, one fill
    fees_open: float


@dataclass
class Decision:
    """`engine.decide`'s answer: an entry or the first reason there is none. `checks` lists every gate
    evaluated, (name, passed, value), in order, so a skip is explainable."""

    arm: str
    enter: bool
    reason: str
    entry: Entry | None = None
    checks: list[tuple[str, bool, object]] = field(default_factory=list)
