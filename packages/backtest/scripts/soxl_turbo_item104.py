"""Item 104: a video's leveraged-fund "turbo" on TQQQ and SOXL: a put spread paying for a year-long call or
call spread, entered on the video's checklist (research/edge-hypotheses-plan.md, section 104).

The declaration is section 104; this script runs it. Results are derived from licensed data and stay under
the backtest results directory, never the repo.

- `listing`: item 103's one-session probes of the year-long monthlies, written for this item
  (`{results}/listing_requests.json`; the fetch skips what item 103's run already holds).
- `plan`: refuses until every probe is answered, then writes the chain paths (`{results}/path_requests.json`)
  and `plan.json`: every session's year-long monthly (the structure and the skew read on it) through its
  45-day close, and every session's 45-day expiry (the implied vol the checklist reads).
- `study`: one run over the whole window, judged by the declared RULE and EXTRA_RULE, written to
  `{results}/results.json` as the kit's verdict block. `study --smoke` runs on the sample fetched so far.

**The structure,** all legs on the listed standard monthly nearest 360 days (240-540, item 103's long rule),
picked by delta among quotes passing item 103's width bar, the nearer the money on a tie:
- `spread`: sell the out-of-the-money put nearest 0.15 delta, buy the put under it nearest 0.10, buy the
  call above the short put nearest 0.50, sell the call above that nearest 0.30;
- `convex`: the same put spread and the 0.50 call, no short call.
Opened at the mid with the suite's slippage on each leg at the entry close, closed the same way on the first
session 45 or fewer days before expiry (an unquoted leg retried the next session; at expiry each leg settles
at intrinsic with the $5 event). One position a name at a time; a split ex-date drops it unbooked, as item
103 drops its long. Capital is the position's maximum loss at entry: the put spread's width less the net
credit (plus the net debit).

**The checklist,** read at each close on the fund's own split-adjusted closes and its chains:
1. `rv`: the 20-session realised vol under its median over the last 252 sessions;
2. `rv_iv`: that realised vol under the implied vol at the money of the expiry 35-60 days out nearest 45;
3. `stretch`: the close at least 30% under its 200-session average;
4. `skew`: on the year-long monthly, the 0.25-delta put's implied vol less the 0.25-delta call's, above the
   90th percentile of its readings over the previous 252 sessions (at least 126 of them).
Gates: `partial` = stretch and skew (the video's entry the day it was made); `full` = all four; `any` = no
gate (reported only). A close where a gate's item cannot be read enters nothing, counted.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cherrypick.core import progress as core_progress
from cherrypick.core import stats as core_stats

from cherrypick.backtest.study import checks as ck
from cherrypick.backtest.study import rule as kit_rule
from cherrypick.backtest.study.inputs import Inputs

_ITEM103 = Path(__file__).resolve().parent / "leaps_diagonal_item103.py"


def _load_item103():
    spec = importlib.util.spec_from_file_location("leaps_diagonal_item103", _ITEM103)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("leaps_diagonal_item103", module)  # its dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


i103 = _load_item103()

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = (
    {
        "on": "2026-11-28",
        "what": "The plan requests each year-long call's path to its expiry, not to its 45-day close. A "
        "close with a leg unquoted waits a session and tries again, and the smoke read such sessions past "
        "the declared close as no chain (the count is in the local smoke result).",
        "why": "An undeclared read is counted and read as no chain, so a waiting close was never priced.",
        "decided": "user asked for the plan fix after the smoke's undeclared reads were shown",
        "before_read": True,
    },
)

#: The kit's three bars on each position's weekly net, one run cut at one date (weeks ending before it are
#: the design). The delta bar applies: the structure is long delta.
RULE = {
    "t_bar": 2.0,
    "min_n": 100,
    "holdout_min_n": 50,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 104},
    "delta": True,
    "splits": ["2024-01-01"],
}

#: What this study judges beyond the kit's bars, and its declared constants.
EXTRA_RULE = {
    "alpha": 0.05,  # Holm across the four judged arms, on the design capital bar's one-sided p
    "halves_split": "2022-01-01",  # design weeks before it, and from it
    "stress_scale": 1.5,  # fees and slippage at this multiple
    "min_entries": 8,  # positions opened in the design, both names together; fewer is no verdict
    "rv_sessions": 20,
    "lookback": 252,
    "stretch": 0.30,  # the close at least this far under its 200-session average
    "sma_sessions": 200,
    "skew_delta": 0.25,
    "skew_pct": 0.90,
    "skew_min_readings": 126,
    "min_two_sided": 0.80,  # the coverage floor, per name and year, from the store's quality report
    "min_strikes": 25,
}

#: The research-queue chain, queued with `run.py queue-chain --script <this file>`.
CHAIN = (
    {"step": "listing", "kind": "script", "args": ["listing"], "output": "{results}/listing_requests.json"},
    {
        "step": "fetch-listing",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/listing_requests.json"},
    },
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/path_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json", "limit": 20},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch-chains",
        "kind": "fetch-chain-paths",
        "task_args": {"requests": "{results}/path_requests.json"},
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

NAMES = i103.NAMES
START = i103.START
#: Each structure's legs: (role, right, quantity, target |delta|). Quantity positive long, negative short.
STRUCTURES = {
    "spread": (("sp", "P", -1, 0.15), ("lp", "P", 1, 0.10), ("lc", "C", 1, 0.50), ("sc", "C", -1, 0.30)),
    "convex": (("sp", "P", -1, 0.15), ("lp", "P", 1, 0.10), ("lc", "C", 1, 0.50)),
}
GATES = {"partial": ("stretch", "skew"), "full": ("rv", "rv_iv", "stretch", "skew"), "any": ()}
ARMS = {f"{s}-{g}": (s, g) for s in STRUCTURES for g in GATES}
JUDGED = ("spread-partial", "spread-full", "convex-partial", "convex-full")
REPORTED = ("spread-any", "convex-any")


# ------------------------------------------------------------------------------------------------
# Pure: the checklist's series.


def adjusted(closes: dict[str, float], split_days: list[str], factors: dict[str, float]) -> dict[str, float]:
    """Raw closes divided by the splits after each date (`factors`: ex-date -> new shares per old)."""
    out = {}
    for d, c in closes.items():
        f = math.prod(factors[s] for s in split_days if s > d)
        out[d] = c / f
    return out


def realised(closes: list[float]) -> float | None:
    """Annualised close-to-close volatility (n closes give n - 1 returns)."""
    if len(closes) < 3 or any(c <= 0 for c in closes):
        return None
    r = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    return statistics.stdev(r) * math.sqrt(252)


def rv_series(days: list[str], adj: dict[str, float], n: int) -> dict[str, float | None]:
    """{session: the realised vol of its last n returns}, None before n + 1 closes."""
    out = {}
    for i, d in enumerate(days):
        out[d] = realised([adj[x] for x in days[i - n : i + 1]]) if i >= n else None
    return out


def sma_gap(days: list[str], adj: dict[str, float], n: int) -> dict[str, float | None]:
    """{session: close / its n-session average - 1}, None before n closes."""
    out = {}
    for i, d in enumerate(days):
        out[d] = (
            adj[d] / statistics.fmean(adj[x] for x in days[i - n + 1 : i + 1]) - 1 if i >= n - 1 else None
        )
    return out


def trailing(
    values: dict[str, float | None], days: list[str], i: int, n: int, *, include: bool
) -> list[float]:
    """The readings of the n sessions up to day i (with it, or the n before it), Nones left out."""
    lo, hi = (i - n + 1, i + 1) if include else (i - n, i)
    return [values[d] for d in days[max(lo, 0) : hi] if values.get(d) is not None]


def percentile(xs: list[float], q: float) -> float:
    """The q quantile by linear interpolation between order statistics."""
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def otm_nearest(chain, right: str, target: float, beyond: float | None = None) -> float | None:
    """The out-of-the-money strike on `right` passing the width bar whose |delta| is nearest `target`,
    strictly beyond `beyond` (below it for a put, above it for a call); the nearer the money on a tie."""
    fwd = chain.forward
    if fwd is None:
        return None
    best = None
    for k, q in chain.by[right].items():
        if (right == "P" and k >= fwd) or (right == "C" and k <= fwd) or not i103.width_ok(q):
            continue
        if beyond is not None and (k >= beyond if right == "P" else k <= beyond):
            continue
        d = chain.delta(q)
        if d is None:
            continue
        key = (round(abs(abs(d) - target), 9), abs(k - fwd))
        if best is None or key < best[0]:
            best = (key, k)
    return None if best is None else best[1]


def call_nearest(chain, target: float, above: float) -> float | None:
    """Any call over `above` passing the width bar whose delta is nearest `target`; the lower on a tie."""
    best = None
    for k, q in chain.by["C"].items():
        if k <= above or not i103.width_ok(q):
            continue
        d = chain.delta(q)
        if d is None:
            continue
        key = (round(abs(d - target), 9), k)
        if best is None or key < best[0]:
            best = (key, k)
    return None if best is None else best[1]


def atm_iv(chain) -> float | None:
    """The mean of the call's and put's implied vol at the strike nearest the forward with both."""
    if chain is None or chain.forward is None:
        return None
    both = []
    for k in chain.by["C"]:
        if k in chain.by["P"]:
            c, p = chain.iv(chain.by["C"][k]), chain.iv(chain.by["P"][k])
            if c is not None and p is not None:
                both.append((abs(k - chain.forward), k, (c + p) / 2))
    return min(both)[2] if both else None


def skew(chain, delta: float) -> float | None:
    """The out-of-the-money put's implied vol nearest `delta` less the call's, any quote two-sided."""
    if chain is None or chain.forward is None:
        return None
    picks = {}
    for right in ("P", "C"):
        best = None
        for k, q in chain.by[right].items():
            if (right == "P" and k >= chain.forward) or (right == "C" and k <= chain.forward):
                continue
            d, iv = chain.delta(q), chain.iv(q)
            if d is None or iv is None:
                continue
            key = (round(abs(abs(d) - delta), 9), abs(k - chain.forward))
            if best is None or key < best[0]:
                best = (key, iv)
        if best is None:
            return None
        picks[right] = best[1]
    return picks["P"] - picks["C"]


def checklist(days: list[str], adj: dict[str, float], iv_at: dict, skew_at: dict) -> dict[str, dict]:
    """{session: {item: True | False | None}} for the four mechanical items."""
    r = EXTRA_RULE
    rv = rv_series(days, adj, r["rv_sessions"])
    gap = sma_gap(days, adj, r["sma_sessions"])
    out = {}
    for i, d in enumerate(days):
        row = {}
        med = trailing(rv, days, i, r["lookback"], include=True)
        row["rv"] = None if rv[d] is None or len(med) < r["lookback"] // 2 else rv[d] < statistics.median(med)
        iv = iv_at.get(d)
        row["rv_iv"] = None if rv[d] is None or iv is None else rv[d] < iv
        row["stretch"] = None if gap[d] is None else gap[d] <= -r["stretch"]
        prior = trailing(skew_at, days, i, r["lookback"], include=False)
        s = skew_at.get(d)
        row["skew"] = (
            None if s is None or len(prior) < r["skew_min_readings"] else s > percentile(prior, r["skew_pct"])
        )
        out[d] = row
    return out


def gate_open(row: dict, gate: str) -> bool | None:
    """All the gate's items true; None when any is unread (and none is false)."""
    items = [row.get(k) for k in GATES[gate]]
    if any(v is False for v in items):
        return False
    return None if any(v is None for v in items) else True


# ------------------------------------------------------------------------------------------------
# Pure: the structure.


def pick_legs(chain, structure: str) -> dict[str, float] | None:
    """{role: strike} for the structure, or None when a leg cannot be picked."""
    sp = otm_nearest(chain, "P", 0.15)
    if sp is None:
        return None
    lp = otm_nearest(chain, "P", 0.10, beyond=sp)
    lc = call_nearest(chain, 0.50, above=sp)
    if lp is None or lc is None:
        return None
    out = {"sp": sp, "lp": lp, "lc": lc}
    if structure == "spread":
        sc = otm_nearest(chain, "C", 0.30, beyond=lc)
        if sc is None:
            return None
        out["sc"] = sc
    return out


def max_loss(strikes: dict[str, float], entry_cash: float) -> float:
    """The position's worst case in dollars: under the long put every call is worthless and the put spread
    owes its width, so the loss is the width less the cash taken in at entry (a debit adds)."""
    return 100.0 * (strikes["sp"] - strikes["lp"]) - entry_cash


# ------------------------------------------------------------------------------------------------
# The replay of one arm on one name.


@dataclass
class Market:
    name: str
    sessions: list[str]
    closes: dict[str, float]  # raw: the chains' basis
    chain_at: object  # (session, expiry) -> Chain | None
    listed_at: object  # (session) -> listed year-long monthlies
    gate_rows: dict  # {session: checklist row}
    split_days: list[str] = field(default_factory=list)


def simulate(m: Market, arm: str, *, scale: float = 1.0) -> i103.Run:
    """One arm on one name, costs at `scale`. Snapshots in item 103's shape, so its weekly rows read them."""
    structure, gate = ARMS[arm]
    legs = STRUCTURES[structure]
    run = i103.Run(m.name, arm)
    ends = i103.week_ends(m.sessions)
    splits = set(m.split_days)
    st = {"cash": 0.0, "pos": None, "cycle": 0, "closed_on": None, "flow": 0.0, "capital": None}

    def book(x: float) -> None:
        st["cash"] += x
        st["flow"] += x

    def q_of(d, exp, right, k):
        ch = m.chain_at(d, exp)
        return None if ch is None else ch.by[right].get(k)

    def enter(d: str) -> None:
        if gate != "any":
            g = gate_open(m.gate_rows.get(d, {}), gate)
            if g is None:
                return run.count("gate_unread")
            if not g:
                return run.count("gate_closed_sessions")
        exp = i103.pick_long_expiry(d, m.listed_at(d))
        if exp is None:
            return run.count("refused_no_listed")
        ch = m.chain_at(d, exp)
        if ch is None:
            return run.count("refused_no_chain")
        strikes = pick_legs(ch, structure)
        if strikes is None:
            return run.count("refused_no_strikes")
        st["cycle"] += 1
        st["flow"] = 0.0
        cash = 0.0
        for role, right, qty, _ in legs:
            q = ch.by[right][strikes[role]]
            cash += -qty * 100.0 * i103.mid(q) - i103.slip(q, scale) - i103.open_fee(m.name, qty < 0, scale)
        book(cash)
        st["pos"] = {"exp": exp, "strikes": strikes, "opened": d}
        st["capital"] = max_loss(strikes, cash)
        run.cycles.append({"cycle": st["cycle"], "opened": d, "exp": exp, "strikes": strikes,
                           "capital": st["capital"], "gate": dict(m.gate_rows.get(d, {}))})  # fmt: skip
        run.count("entries")

    def close(d: str) -> None:
        pos = st["pos"]
        quotes = {role: q_of(d, pos["exp"], right, pos["strikes"][role]) for role, right, _, _ in legs}
        if d < pos["exp"] and not all(i103.markable(q) for q in quotes.values()):
            return run.count("close_unquoted")
        s = m.closes[d]
        for role, right, qty, _ in legs:
            q, k = quotes[role], pos["strikes"][role]
            if i103.markable(q):
                book(qty * 100.0 * i103.mid(q) - i103.slip(q, scale) - i103.close_fee(m.name, qty > 0, scale))
            else:
                iv = max(0.0, s - k) if right == "C" else max(0.0, k - s)
                book(qty * 100.0 * iv - (i103.settle_fee(scale) if iv > 0 else 0.0))
        run.cycles[-1].update({"closed": d})
        st["pos"], st["capital"], st["closed_on"] = None, None, d

    def snapshot(d: str) -> None:
        pos = st["pos"]
        row = {
            "end": d,
            "has_pos": pos is not None,
            "cycle": st["cycle"] if pos else None,
            "spot": m.closes[d],
        }
        if pos is None:
            run.snaps.append({**row, "priced": True, "value": st["cash"], "capital": None, "delta": 0.0})
            return
        ch = m.chain_at(d, pos["exp"])
        value, delta = st["cash"], 0.0
        for role, right, qty, _ in legs:
            q = None if ch is None else ch.by[right].get(pos["strikes"][role])
            if not i103.markable(q):
                run.count("unpriced_weeks")
                run.snaps.append({**row, "priced": False})
                return
            value += qty * 100.0 * i103.mid(q)
            dl = ch.delta(q)
            delta = None if dl is None or delta is None else delta + qty * dl
        run.snaps.append({**row, "priced": True, "value": value, "capital": st["capital"], "delta": delta})

    for d in m.sessions:
        if st["pos"] is not None and d in splits:
            run.dropped.add(st["cycle"])
            st["cash"] -= st["flow"]
            run.cycles[-1].update({"closed": d, "dropped": True})
            st["pos"], st["capital"] = None, None
            run.count("split_drops")
        if st["pos"] is not None and i103.long_due(d, st["pos"]["exp"]):
            close(d)
        if st["pos"] is None and (st["closed_on"] is None or d > st["closed_on"]):
            enter(d)
        if d in ends:
            snapshot(d)
    return run


# ------------------------------------------------------------------------------------------------
# The verdict.


def _pos(x) -> bool:
    return x is not None and x > 0


def _excess_mean(rows: list[dict]) -> float | None:
    xs = [r["net"] - ck.bill_on(r["bp"], r["rate"], r["days"]) for r in rows]
    return statistics.fmean(xs) if xs else None


def judge_arm(rule: dict, rows: list[dict], rows_stress: list[dict], entries: int, holm_ok: bool,
              coverage_short: list[str]) -> dict:  # fmt: skip
    """One judged arm: the kit's three bars on the design weeks, then its halves, names, stress, entries and
    Holm; the holdout read only after all pass."""
    split, half = rule["splits"][0], EXTRA_RULE["halves_split"]
    d_rows = [r for r in rows if r["date"] < split]
    h_rows = [r for r in rows if r["date"] >= split]
    extra = {
        "holm": holm_ok,
        "halves": all(
            _pos(_excess_mean([r for r in d_rows if (r["date"] < half) == lo])) for lo in (True, False)
        ),
        "names": all(_pos(_excess_mean([r for r in d_rows if r["name"] == n])) for n in NAMES),
        "stress": _pos(_excess_mean([r for r in rows_stress if r["date"] < split])),
    }
    kit = kit_rule.judge_part(rule, d_rows, rule["min_n"])
    part = {"from": None, "before": split, "n": len(d_rows), **kit, "extra": extra, "entries": entries}
    if coverage_short:
        word, why = ck.NO_VERDICT, "coverage: " + "; ".join(coverage_short)
    elif entries < EXTRA_RULE["min_entries"]:
        word, why = (
            ck.NO_VERDICT,
            f"{entries} positions opened in the design, under {EXTRA_RULE['min_entries']}",
        )
    elif kit["verdict"] != ck.PASS:
        word, why = kit["verdict"], "the kit's bars: " + ", ".join(kit["failed"]) if kit["failed"] else None
    elif not all(extra.values()):
        word, why = ck.FAIL, "beyond the kit: " + ", ".join(k for k, v in extra.items() if not v)
    else:
        word, why = ck.PASS, None
    part["verdict"] = word
    if why:
        part["why"] = why
    parts = [part]
    if word != ck.PASS:
        parts.append({"from": split, "before": None, "n": len(h_rows), "verdict": kit_rule.NOT_READ})
    else:
        hk = kit_rule.judge_part(rule, h_rows, rule["holdout_min_n"], so_far=d_rows + h_rows)
        word = hk["verdict"]
        parts.append({"from": split, "before": None, "n": len(h_rows), **hk})
    return {"n": len(rows), "parts": parts, "verdict": word}


def judge(rule: dict, rows: dict, rows_stress: dict, entries: dict, coverage_short: list[str]) -> dict:
    split = rule["splits"][0]
    pvals = {}
    for a in JUDGED:
        d = [r for r in rows[a] if r["date"] < split]
        cap = ck.capital_check([r["net"] for r in d], [r["bp"] for r in d], [r["days"] for r in d],
                               [r["rate"] for r in d], t_bar=rule["t_bar"], min_n=rule["min_n"])  # fmt: skip
        pvals[a] = cap["p"] if cap.get("p") is not None and cap["n"] >= rule["min_n"] else 1.0
    holm = core_stats.holm(pvals, EXTRA_RULE["alpha"])
    arms = {a: judge_arm(rule, rows[a], rows_stress[a], entries[a], holm[a], coverage_short) for a in JUDGED}
    return {
        "rule": rule,
        "extra_rule": EXTRA_RULE,
        "arms": arms,
        "verdict": {a: b["verdict"] for a, b in arms.items()},
    }


# ------------------------------------------------------------------------------------------------
# The stores and the commands.


def out_dir() -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / "item104"
    d.mkdir(parents=True, exist_ok=True)
    return d


def split_factors(sym: str) -> dict[str, float]:
    from cherrypick.technicals import adjust, store
    from cherrypick.technicals import paths as tpaths

    hist = store.connect(tpaths.history_db())
    try:
        raw = store.raw_bars(hist, sym)
        return {
            s.ex_date: s.to_factor / s.for_factor for s in adjust.dedupe_splits(raw, store.splits(hist, sym))
        }
    finally:
        hist.close()


def path_requests(sym: str, sessions: list[str], listed_at, expiries: list[str]) -> list[dict]:
    """Every session's year-long monthly from the first session it is picked to its expiry, and every
    session's 45-day expiry over the sessions it is read on. A long closes 45 days out, but a close with a
    leg unquoted waits a session and tries again, up to the expiry, where it settles: so the whole tail."""
    span: dict[str, list[str]] = {}

    def add(e: str, a: str, b: str) -> None:
        if a > b:
            return
        have = span.setdefault(e, [a, b])
        have[0], have[1] = min(have[0], a), max(have[1], b)

    for i, d in enumerate(sessions):
        e = i103.pick_long_expiry(d, listed_at(d))
        if e is not None:
            add(e, d, next((x for x in sessions[i:] if x >= e), sessions[-1]))
        se = i103.pick_short_expiry(d, expiries, "9999-12-31")
        if se is not None:
            add(se, d, d)
    return [{"symbol": sym, "expiration": e, "start": a, "through": b} for e, (a, b) in sorted(span.items())]


def cmd_listing(args) -> int:
    end = i103.window_end()
    reqs = i103.listing_requests(i103.load_expiries(), end)
    path = Path(args.out) if args.out else out_dir() / "listing_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(reqs), "end": end}))
    return 0


def cmd_plan(args) -> int:
    listing = out_dir() / "listing_requests.json"
    if not listing.exists():
        raise SystemExit(f"no listing probes at {listing}: run `soxl_turbo_item104.py listing` first")
    probes = json.loads(listing.read_text(encoding="utf-8"))["requests"]
    conn = i103.chain_store()
    try:
        if i103.answered(conn, probes):
            print(json.dumps({"listing_unanswered": len(i103.answered(conn, probes))}))
            return 2
        end = max(r["through"] for r in probes)
        expiries = i103.load_expiries()
        inputs = Inputs(listing)
        out, counts = [], {}
        for sym in core_progress.track(NAMES, job="item104-plan", phase="names", unit="names",
                                       key=lambda s: s, current=str):  # fmt: skip
            closes, _ = i103.load_bars(sym)
            sessions = i103.window_sessions(closes, end)
            listed_at = i103.make_listed_at(inputs, conn, sym, sessions, expiries[sym])
            reqs = path_requests(sym, sessions, listed_at, expiries[sym])
            counts[sym] = len(reqs)
            out += reqs
    finally:
        conn.close()
    (out_dir() / "plan.json").write_text(
        json.dumps({"start": START, "end": end, "paths": counts}), encoding="utf-8"
    )
    path = Path(args.out) if args.out else out_dir() / "path_requests.json"
    path.write_text(json.dumps({"requests": out}, indent=1), encoding="utf-8")
    print(json.dumps({"written": str(path), "requests": len(out), "per_name": counts}))
    return 0


def coverage(years: list[str]) -> list[str]:
    from cherrypick.backtest import paths, storequality

    path = paths.results_dir() / "quality" / "eod_chains.json"
    if not path.exists():
        return [f"no store quality report at {path}"]
    doc = json.loads(path.read_text(encoding="utf-8"))
    short = []
    for sym in NAMES:
        short += storequality.meets(
            doc, sym, years, min_two_sided=EXTRA_RULE["min_two_sided"], min_strikes=EXTRA_RULE["min_strikes"]
        )
    return short


def build_market(sym: str, inputs: Inputs, listing: Inputs, conn, end: str, expiries: list[str],
                 undeclared: list) -> Market:  # fmt: skip
    """One name's market and its checklist, read from the declared paths."""
    closes, split_days = i103.load_bars(sym)
    factors = split_factors(sym)
    every = sorted(closes)
    adj = adjusted(closes, split_days, factors)
    sessions = i103.window_sessions(closes, end)
    listed_at = i103.make_listed_at(listing, conn, sym, sessions, expiries)
    chain_at = i103.make_chain_at(inputs, conn, sym, undeclared)
    iv_at, skew_at = {}, {}
    for d in sessions:
        se = i103.pick_short_expiry(d, expiries, "9999-12-31")
        iv_at[d] = atm_iv(chain_at(d, se)) if se else None
        e = i103.pick_long_expiry(d, listed_at(d))
        skew_at[d] = skew(chain_at(d, e), EXTRA_RULE["skew_delta"]) if e else None
    # the bar-based items read history before the window: realised vol and the average need it
    rows = checklist(every, adj, iv_at, skew_at)
    return Market(sym, sessions, closes, chain_at, listed_at, {d: rows[d] for d in sessions},
                  split_days=[s for s in split_days if START <= s <= end])  # fmt: skip


def cmd_study(args) -> int:
    inputs = Inputs(out_dir() / "path_requests.json")
    listing = Inputs(out_dir() / "listing_requests.json")
    plan = json.loads((out_dir() / "plan.json").read_text(encoding="utf-8"))
    conn = i103.chain_store()
    rate_at = i103.bill_reader()
    expiries = i103.load_expiries()
    undeclared: list = []
    job = "item104-smoke" if args.smoke else "item104-study"
    runs, stress = {a: {} for a in ARMS}, {a: {} for a in ARMS}
    try:
        with core_progress.Reporter(job) as rep:
            rep.split("runs", [s for s in NAMES for _ in range(2 * len(ARMS) + 1)], unit="runs")
            for sym in NAMES:
                mk = build_market(sym, inputs, listing, conn, plan["end"], expiries[sym], undeclared)
                rep.advance("runs", key=sym, current=f"{sym} checklist")
                for a in ARMS:
                    runs[a][sym] = simulate(mk, a)
                    rep.advance("runs", key=sym, current=f"{sym} {a}")
                    stress[a][sym] = simulate(mk, a, scale=EXTRA_RULE["stress_scale"])
                    rep.advance("runs", key=sym, current=f"{sym} {a} stress")
            read = {"paths": inputs.fingerprint(conn), "listing": listing.fingerprint(conn)}
    finally:
        conn.close()
    split = RULE["splits"][0]
    rows = {a: [r for s in NAMES for r in i103.weekly_rows(runs[a][s], rate_at)] for a in ARMS}
    rows_s = {a: [r for s in NAMES for r in i103.weekly_rows(stress[a][s], rate_at)] for a in ARMS}
    entries = {a: sum(1 for s in NAMES for c in runs[a][s].cycles if c["opened"] < split) for a in ARMS}
    years = [str(y) for y in range(int(START[:4]), int(plan["end"][:4]) + 1)]
    block = judge(RULE, rows, rows_s, entries, coverage(years))
    doc = {"licence": "derived from licensed vendor data: never commit or publish", "item": 104, **block}
    doc.update({
        "reported": {a: i103.report_arm(rows[a], RULE) for a in ARMS},
        "counts": {"undeclared": len(undeclared), "window": [START, plan["end"]], "entries": entries,
                   "runs": {a: {s: runs[a][s].counts for s in NAMES} for a in ARMS},
                   "weeks": {a: {s: i103.week_ledger(runs[a][s]) for s in NAMES} for a in ARMS}},
        "inputs": read,
        "positions": {a: {s: runs[a][s].cycles for s in NAMES} for a in ARMS},
    })  # fmt: skip
    report: dict = {}
    if args.smoke:
        doc = {**doc, "smoke": True, "verdict": "smoke", "warmup_arms": smoke_warmup_arms()}
        report = smoke_refusal(runs)
    name = "smoke.json" if args.smoke else "results.json"
    path = Path(args.out) if args.out else out_dir() / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"], **report}))
    return 2 if report.get("refused") else 0


def smoke_warmup_arms() -> dict[str, str]:
    """The judged arms a smoke's sample cannot open, with why, for the research queue: every gated arm,
    since its gate reads the trailing year's realised vol and skew, which a sample of paths does not hold."""
    why = (
        f"its gate reads {EXTRA_RULE['skew_min_readings']} prior skew readings and a "
        f"{EXTRA_RULE['lookback']}-session realised vol, which the smoke's sample does not hold"
    )
    return dict.fromkeys(JUDGED, why)


def smoke_refusal(runs: dict) -> dict:
    """{"refused": why} when the ungated arms opened nothing on the sample: then the smoke proved no path
    reads, and the excused gated arms prove nothing either. Empty when they opened a position."""
    opened = sum(runs[a][s].counts.get("entries", 0) for a in REPORTED for s in runs[a])
    return {} if opened else {"refused": "the ungated arms opened no position on the sample"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("listing", cmd_listing), ("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="run on the sample fetched so far")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
