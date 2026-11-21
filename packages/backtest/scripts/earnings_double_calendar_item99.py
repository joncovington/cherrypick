"""Item 99: a double calendar across an earnings report, strikes a declared share of its expected move.

The declaration is research/edge-hypotheses-plan.md section 99; this script runs it. Results are
derived from licensed data and stay under the backtest results directory, never the repo.

The earnings module's retired `double_calendar` book sold the front expiry's call and put near
spot +/- one expected move and bought them in a monthly 21 days or more later. This study asks
whether that structure earns at a declared share of the report's own expected move, priced at the
times the module trades: in at the 15:40 ET five-minute bar the session before the report, out at
10:00 on the reaction morning.

- `plan`: item 31's events (extended past its last report to reaction sessions on or before
  2026-11-20), each one's expiries and control session (item 31's `front_expiry`, `back_expiry`,
  `control_for`), and the intraday requests (`{results}/intraday_requests.json`, newest first; its
  first 200 also in `{results}/intraday_sample.json`). It also writes `planned_mde` from item 52's
  B1 trades.
- `study`: one run over every event, judged by RULE (the study kit's three bars, design and
  holdout) and EVENT_RULE (the paired control, the halves, the stress, the best five, then the
  holdout and the confirmation part). `--smoke` reads the sample and writes counts only.

Item 31's functions are loaded by path, so the events, the sessions, the expiries and the control
cannot drift from it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from cherrypick.core import calendar as cal
from cherrypick.core import fees, progress, robustness, treasury

from cherrypick.backtest.study import rule as kit_rule

_spec = importlib.util.spec_from_file_location(
    "earnings_item31", Path(__file__).with_name("earnings_item31.py")
)
e31 = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("earnings_item31", e31)
_spec.loader.exec_module(e31)

#: The correction log: one entry per fix to this replay, oldest first (orchestrator/studyversion.py).
REPLAY_CORRECTIONS = ()

#: The arms judged (Holm across them) and the neighbours reported beside them: k x EM either side.
ARMS = {"k050": 0.5, "k075": 0.75, "k100": 1.0}
NEIGHBOURS = {"k025": 0.25, "k125": 1.25}

#: The trade as declared.
SPEC = {
    "entry_bar": "15:40",
    "exit_bar": "10:00",
    "exit_latest": "10:30",  # a leg unquoted at 10:00 takes the next quoted bar up to here
    "bar_minutes": 5,
    "strike_tolerance_em": 0.25,  # a strike further than this from its target drops the arm
    "max_spread_frac_of_mid": 0.10,  # each leg at entry
    "strike_range": 30,  # strikes either side of the money asked of the vendor
    "back_min_days": 21,  # the module's select_back_expiration
    "extend_reaction_through": "2026-11-20",
    "sample_requests": 200,
    "reported_bars": {
        "r0945": ("reaction", "09:45"),
        "r1555": ("reaction", "15:55"),
        "n1555": ("next", "15:55"),
    },  # fmt: skip
}

#: The kit's three bars, per arm, one row per trade entered before the confirmation part.
RULE = {
    "t_bar": 2.4,
    "min_n": 150,
    "holdout_min_n": 100,
    "tail": {"per_year": "sample", "resamples": 10_000, "seed": 99},
    "delta": False,
    "splits": ["2024-01-01"],
}

#: The second rule: the report must be what earns, in each part.
EVENT_RULE = {
    "t_bar": 2.4,
    "holm_alpha": 0.05,
    "resamples": 2000,
    "halves": "2022-07-01",
    "confirmation_from": "2026-07-15",
    "best_removed": 5,
    "stress": {"fees": 1.5, "slippage_frac_of_spread": 0.5, "slippage_cap_frac_of_mid": None},
    "coverage_floor": 0.80,
}

#: The research-queue chain. The sample's requests are fetched and smoke-read first; the full fetch
#: runs twice so the second pass retries what the first could not get.
CHAIN = (
    {"step": "plan", "kind": "script", "args": ["plan"], "output": "{results}/intraday_requests.json"},
    {
        "step": "fetch-sample",
        "kind": "fetch-intraday-chains",
        "task_args": {"requests": "{results}/intraday_sample.json", "workers": 2},
    },
    {"step": "smoke", "kind": "script", "args": ["study", "--smoke"], "output": "{results}/smoke.json"},
    {
        "step": "fetch",
        "kind": "fetch-intraday-chains",
        "task_args": {"requests": "{results}/intraday_requests.json", "workers": 2},
        "repeat": 2,
    },
    {"step": "study", "kind": "script", "args": ["study"], "output": "{results}/results.json"},
)

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
RATE_STALE_DAYS = 10
LONG, SHORT = 1, -1


# --------------------------------------------------------------------------- pure: dates and requests


def bar_after(bar: str, minutes: int) -> str:
    t = datetime(2000, 1, 1, int(bar[:2]), int(bar[3:])) + timedelta(minutes=minutes)
    return t.strftime("%H:%M")


def exit_bars(spec: dict = SPEC) -> list[str]:
    """The judged exit bar and the later bars an unquoted leg may take, in order."""
    out, b = [], spec["exit_bar"]
    while b <= spec["exit_latest"]:
        out.append(b)
        b = bar_after(b, spec["bar_minutes"])
    return out


def trade_dates(event: dict, spec: dict = SPEC) -> dict | None:
    """The sessions and expiries of one event (or control): item 31's rules."""
    entry = date.fromisoformat(event["entry_session"])
    report = date.fromisoformat(event["report_date"])
    front = e31.front_expiry(report, event["timing"], entry)
    if front is None:
        return None
    back = e31.back_expiry(front, e31.weekly_candidates(entry), spec["back_min_days"])
    if back is None:
        return None
    reaction = e31.reaction_session(report, event["timing"])
    return {"entry": entry, "reaction": reaction, "next": cal.next_trading_day(reaction),
            "front": front, "back": back}  # fmt: skip


def requests_for(symbol: str, d: dict, *, with_next: bool, spec: dict = SPEC) -> list[dict]:
    """The intraday requests one priced window reads: both expiries on the entry session's window and
    the reaction session's, and (events only) the next session's. Each names the bar it must hold."""
    out = []
    windows = [("entry", d["entry"], e31.ENTRY_WINDOW, spec["entry_bar"])]
    start, end = e31.later_window(d["reaction"])
    windows.append(("reaction", d["reaction"], (start, end), spec["exit_bar"]))
    if with_next:
        s2, e2 = e31.later_window(d["next"])
        windows.append(("next", d["next"], (s2, e2), e2))
    for kind, session, (a, b), need in windows:
        for exp in (d["front"], d["back"]):
            out.append(
                {
                    "symbol": symbol,
                    "session": session.isoformat(),
                    "expiration": exp.isoformat(),
                    "start": a,
                    "end": b,
                    "interval": e31.INTERVAL,
                    "strike_range": spec["strike_range"],
                    "kind": kind,
                    "required": [need],
                    "year": d["entry"].year,
                }
            )
    return out


# --------------------------------------------------------------------------- pure: quotes and strikes


def two_sided(q: dict | None) -> bool:
    return (
        bool(q)
        and q.get("bid") is not None
        and q.get("ask") is not None
        and q["bid"] > 0
        and q["ask"] >= q["bid"]
    )


def mid(q: dict) -> float:
    return (q["bid"] + q["ask"]) / 2.0


def spread_ok(q: dict | None, frac: float) -> bool:
    return two_sided(q) and (q["ask"] - q["bid"]) <= frac * mid(q)


def atm_strike(front: dict, back: dict, spot: float) -> float | None:
    """The strike nearest spot with the front's call and put and the back's call two-sided and each
    call carrying an implied volatility: the module's ATM read. The lower on a tie."""
    ks = [
        k
        for (r, k), q in front.items()
        if r == "C"
        and two_sided(q)
        and two_sided(front.get(("P", k)))
        and two_sided(back.get(("C", k)))
        and (q.get("iv") or 0) > 0
        and (back[("C", k)].get("iv") or 0) > 0
    ]
    return min(ks, key=lambda k: (abs(k - spot), k)) if ks else None


def pick_strike(listed: list[float], target: float, spot: float, em: float, right: str, tol: float):
    """(strike, None) for the listed strike nearest `target` on its own side of spot (a call above,
    a put below), or (None, why). `listed` are the strikes quoted in both expiries."""
    side = [k for k in listed if (k > spot if right == "C" else k < spot)]
    if not side:
        return None, "no_strike"
    k = min(side, key=lambda x: (abs(x - target), x))
    if abs(k - target) > tol * em:
        return None, "strike_off_target"
    return k, None


def legs_for(rights: tuple[str, ...], call_k: float | None, put_k: float | None) -> list[tuple]:
    """(side, expiry role, right, strike) for the chosen sides: short the front, long the back."""
    out = []
    for r in rights:
        k = call_k if r == "C" else put_k
        out += [(SHORT, "front", r, k), (LONG, "back", r, k)]
    return out


def intrinsic(right: str, k: float, spot: float) -> float:
    return max(spot - k, 0.0) if right == "C" else max(k - spot, 0.0)


def adverse_exit(side: int, right: str, k: float, spot: float, entry_q: dict) -> float:
    """Item 52's close for a leg with no two-sided exit quote, per share: a short bought back at
    intrinsic plus its entry spread, a long sold at the larger of zero and intrinsic less it."""
    width = entry_q["ask"] - entry_q["bid"]
    v = intrinsic(right, k, spot)
    return v + width if side == SHORT else max(0.0, v - width)


def costs_cfg(stress: dict | None) -> tuple[dict, float]:
    if not stress:
        return {}, 1.0
    knobs = {"slippage_frac_of_spread": stress["slippage_frac_of_spread"]}
    if "slippage_cap_frac_of_mid" in stress:
        knobs["slippage_cap_frac_of_mid"] = stress["slippage_cap_frac_of_mid"]
    return {"tastytrade_costs": knobs}, stress["fees"]


def price(symbol: str, legs: list[tuple], entry_q: list[dict], exit_px: list[dict], *,
          fill: str = "verdict", stress: dict | None = None) -> dict:  # fmt: skip
    """One position, in at the entry quotes and out at `exit_px` (per leg, {"quote": q} or
    {"adverse": price per share}). Fills: `verdict` (mid less the suite's slippage, `stress` as
    declared), `mid` (no slippage), `natural` (longs at the ask and shorts at the bid in, the other
    side out). Fees from core each way. Returns paid, net and ret per dollar paid."""
    cfg, fee_x = costs_cfg(stress)
    n, sells = len(legs), sum(1 for leg in legs if leg[0] == SHORT)
    open_fee = fee_x * fees.ic_open_fee(symbol, 1, legs=n, sell_legs=sells, ndigits=4)
    close_fee = fee_x * fees.ic_close_fee(symbol, 1, legs=n, sell_legs=sells, ndigits=4)
    if fill == "natural":
        debit = sum(q["ask"] if leg[0] == LONG else -q["bid"] for leg, q in zip(legs, entry_q, strict=True))
        slip_in = 0.0
    else:
        debit = sum(leg[0] * mid(q) for leg, q in zip(legs, entry_q, strict=True))
        slip_in = fees.slippage_dollars(entry_q, 1, cfg) if fill == "verdict" else 0.0
    if debit <= 0:
        return {"skip": "no_debit"}
    paid = 100.0 * debit + slip_in + open_fee
    value, quoted = 0.0, []
    for leg, x in zip(legs, exit_px, strict=True):
        if "adverse" in x:
            value += leg[0] * x["adverse"]
            continue
        q = x["quote"]
        quoted.append(q)
        if fill == "natural":
            value += q["bid"] if leg[0] == LONG else -q["ask"]
        else:
            value += leg[0] * mid(q)
    slip_out = fees.slippage_dollars(quoted, 1, cfg) if fill == "verdict" and quoted else 0.0
    got = 100.0 * value - slip_out - close_fee
    net = got - paid
    return {"paid": paid, "net": net, "ret": net / paid, "costs": slip_in + slip_out + open_fee + close_fee}


# --------------------------------------------------------------------------- pure: the rule


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _pos(x) -> bool:
    return x is not None and x > 0


def holm(pvals: dict[str, float], alpha: float) -> dict[str, bool]:
    """Holm's step-down at `alpha`: which of the named p-values are rejected."""
    order = sorted(pvals, key=lambda a: pvals[a])
    out, m, stop = {}, len(order), False
    for i, a in enumerate(order):
        ok = not stop and pvals[a] <= alpha / (m - i)
        stop = stop or not ok
        out[a] = ok
    return out


def part_of(entry: str, rule: dict = RULE, event_rule: dict = EVENT_RULE) -> str:
    if entry >= event_rule["confirmation_from"]:
        return "confirmation"
    return "holdout" if entry >= rule["splits"][0] else "design"


def part_stats(trades: list[dict], event_rule: dict = EVENT_RULE) -> dict:
    """One arm in one part: the paired test against the control (clustered by entry session), the
    time-halves, the name-halves, the stress and the mean without the best five, on return per
    dollar paid."""
    rets = [t["ret"] for t in trades]
    pairs = [t for t in trades if t.get("control_ret") is not None]
    k = event_rule["best_removed"]
    return {
        "trades": len(trades),
        "pairs": len(pairs),
        "mean_ret": _mean(rets),
        "mean_net": _mean(t["net"] for t in trades),
        "pair": e31.cluster_t(
            [t["ret"] - t["control_ret"] for t in pairs],
            [t["entry"] for t in pairs],
            resamples=event_rule["resamples"],
            seed=99,
        ),
        "time_halves": [
            _mean(t["ret"] for t in trades if t["entry"] < event_rule["halves"]),
            _mean(t["ret"] for t in trades if t["entry"] >= event_rule["halves"]),
        ],
        "name_halves": [
            _mean(t["ret"] for t in trades if robustness.name_half(t["symbol"]) == h) for h in (0, 1)
        ],
        "stress_mean": _mean(t.get("stress_ret") for t in trades),
        "without_best": _mean(sorted(rets)[:-k] if len(rets) > k else []),
    }


def judge_event(
    parts: dict[str, dict], holm_ok: bool, rule: dict = RULE, event_rule: dict = EVENT_RULE
) -> dict:
    """EVENT_RULE for one arm: the design checks, then the holdout after a design pass, then the
    confirmation after a holdout pass."""
    d = parts["design"]
    if d["trades"] < rule["min_n"]:
        return {"verdict": NO_VERDICT, "why": f"{d['trades']} design trades < {rule['min_n']}",
                "holdout": NOT_READ, "confirmation": NOT_READ}  # fmt: skip
    checks = {
        "pair_t": _pos(d["pair"]["mean"]) and (d["pair"]["t"] or 0) >= event_rule["t_bar"],
        "pair_holm": holm_ok,
        "time_halves": all(_pos(h) for h in d["time_halves"]),
        "name_halves": all(_pos(h) for h in d["name_halves"]),
        "stress": _pos(d["stress_mean"]),
        "without_best": _pos(d["without_best"]),
    }
    if not all(checks.values()):
        return {"verdict": FAIL, "checks": checks, "holdout": NOT_READ, "confirmation": NOT_READ}
    h = parts["holdout"]
    if h["trades"] < rule["holdout_min_n"]:
        return {"verdict": NO_VERDICT, "checks": checks, "why": f"{h['trades']} holdout trades < "
                f"{rule['holdout_min_n']}", "holdout": NOT_READ, "confirmation": NOT_READ}  # fmt: skip
    if not (_pos(h["mean_ret"]) and _pos(h["pair"]["mean"])):
        return {"verdict": FAIL, "checks": checks, "holdout": "not positive", "confirmation": NOT_READ}
    c = parts["confirmation"]
    held = _pos(c["mean_net"])
    return {"verdict": PASS if held else FAIL, "checks": checks, "holdout": "positive",
            "confirmation": "positive" if held else "not positive"}  # fmt: skip


def both(a: str, b: str) -> str:
    if a == PASS and b == PASS:
        return PASS
    if FAIL in (a, b):
        return FAIL
    return NO_VERDICT


def kit_rows(trades: list[dict], rate) -> list[dict]:
    return [
        {"date": t["entry"], "net": t["net"], "bp": t["paid"], "days": t["days"], "rate": rate(t["entry"])}
        for t in trades
    ]


def judge(trades: dict[str, list[dict]], rate, rule: dict = RULE, event_rule: dict = EVENT_RULE) -> dict:
    """Both rules for every arm, from one run cut into its three parts."""
    conf = event_rule["confirmation_from"]
    bars = kit_rule.evaluate(
        rule, {a: kit_rows([t for t in ts if t["entry"] < conf], rate) for a, ts in trades.items()}
    )
    parts = {
        a: {p: part_stats([t for t in ts if part_of(t["entry"], rule, event_rule) == p], event_rule)
            for p in ("design", "holdout", "confirmation")}
        for a, ts in trades.items()
    }  # fmt: skip
    holm_ok = holm({a: parts[a]["design"]["pair"]["p"] for a in trades}, event_rule["holm_alpha"])
    event = {a: {**parts[a], **judge_event(parts[a], holm_ok[a], rule, event_rule)} for a in trades}
    return {**bars, "event": event,
            "verdict": {a: both(bars["verdict"][a], event[a]["verdict"]) for a in trades}}  # fmt: skip


def judged_years(coverage: dict, floor: float) -> set[int]:
    """The entry years whose requests the store answers complete at or above the floor."""
    return {
        int(y)
        for y, c in coverage["years"].items()
        if c["share_complete"] is not None and c["share_complete"] >= floor
    }


def move_bucket(move_em: float | None) -> str | None:
    if move_em is None:
        return None
    a = abs(move_em)
    return "under_0.5" if a < 0.5 else "0.5_to_1" if a < 1.0 else "over_1"


# --------------------------------------------------------------------------- the store


def results_dir(item: str = "item99") -> Path:
    from cherrypick.backtest import paths

    d = paths.results_dir() / "research" / item
    d.mkdir(parents=True, exist_ok=True)
    return d


def intraday_store() -> sqlite3.Connection:
    from cherrypick.backtest import intradayfetch, settings

    db = intradayfetch.db_path(settings.load(None))
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


class Bars:
    """The intraday store read one (name, session, expiry, bar) at a time, cached: {(right, strike):
    {bid, ask, iv, delta, underlying}}."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._cache: dict[tuple, dict] = {}

    def chain(self, symbol: str, session: date, expiry: date, bar: str) -> dict:
        key = (symbol.upper(), session.isoformat(), expiry.isoformat(), bar)
        if key not in self._cache:
            self._cache[key] = {
                (str(r)[0].upper(), float(k)): {"bid": b, "ask": a, "iv": iv, "delta": dl, "underlying": u}
                for k, r, b, a, iv, dl, u in self.conn.execute(
                    "SELECT strike, right, bid, ask, iv, delta, underlying FROM intraday_chain_quotes "
                    "WHERE symbol = ? AND session = ? AND expiration = ? AND bar = ?",
                    key,
                )
            }
        return self._cache[key]

    def spot(self, symbol: str, session: date, bar: str, *expiries: date) -> float | None:
        """The vendor's own underlying price when the store holds one, else the put-call parity
        forward of the first expiry that has a pair (the current tier stores the underlying only on
        an expiry's own day)."""
        for x in expiries:
            for q in self.chain(symbol, session, x, bar).values():
                if q.get("underlying"):
                    return float(q["underlying"])
        for x in expiries:
            f = parity_spot(self.chain(symbol, session, x, bar))
            if f is not None:
                return f
        return None


def parity_spot(chain: dict) -> float | None:
    """`chaindelta.forward_from_parity` over the chain's two-sided quotes (zero rate, so the forward
    stands for spot, as the store's own greeks take it)."""
    from cherrypick.core import chaindelta

    rows = [
        {"strike": k, "right": r, "bid": q["bid"], "ask": q["ask"]}
        for (r, k), q in chain.items()
        if two_sided(q)
    ]
    return chaindelta.forward_from_parity(rows) if rows else None


# --------------------------------------------------------------------------- one window


def setup(bars: Bars, symbol: str, d: dict, spec: dict = SPEC) -> tuple[dict | None, str | None]:
    """The entry state of one window: spot, the expected move, the strikes quoted in both expiries."""
    from cherrypick.earnings import scanner

    eb = spec["entry_bar"]
    front, back = (
        bars.chain(symbol, d["entry"], d["front"], eb),
        bars.chain(symbol, d["entry"], d["back"], eb),
    )
    if not front:
        return None, "front_unlisted"
    if not back:
        return None, "back_unlisted"
    spot = bars.spot(symbol, d["entry"], eb, d["front"], d["back"])
    if spot is None:
        return None, "no_spot"
    k0 = atm_strike(front, back, spot)
    if k0 is None:
        return None, "no_atm"
    em = scanner.resolve_expected_move(
        mid(front[("C", k0)]),
        mid(front[("P", k0)]),
        front[("C", k0)]["iv"],
        back[("C", k0)]["iv"],
        spot,
        (d["front"] - d["entry"]).days,
        (d["back"] - d["entry"]).days,
        method="event_variance",
    )
    if not em.get("expected_move_dollars") or em["expected_move_dollars"] <= 0:
        return None, "no_expected_move"
    frac = spec["max_spread_frac_of_mid"]
    listed = {
        r: sorted(
            k
            for (rr, k), q in front.items()
            if rr == r and spread_ok(q, frac) and spread_ok(back.get((r, k)), frac)
        )
        for r in ("C", "P")
    }
    return {"spot": spot, "em": em, "front": front, "back": back, "listed": listed}, None


def exit_quotes(bars: Bars, symbol: str, d: dict, legs: list[tuple], entry_q: list[dict], session_key: str,
                bar_list: list[str]) -> tuple[list[dict] | None, int, float | None, str | None]:  # fmt: skip
    """Each leg's exit at the first bar in `bar_list` where it is two-sided, else item 52's adverse
    close against the spot at the first bar. (exits, adverse legs, spot, why-skipped)."""
    session = d[session_key]
    spot = None
    for b in bar_list:
        spot = bars.spot(symbol, session, b, d["front"], d["back"])
        if spot is not None:
            break
    if spot is None:
        return None, 0, None, "no_exit_spot"
    out, adverse = [], 0
    for leg, q0 in zip(legs, entry_q, strict=True):
        side, role, right, k = leg
        got = None
        for b in bar_list:
            q = bars.chain(symbol, session, d[role], b).get((right, k))
            if two_sided(q):
                got = {"quote": q}
                break
        if got is None:
            got = {"adverse": adverse_exit(side, right, k, spot, q0)}
            adverse += 1
        out.append(got)
    return out, adverse, spot, None


def window_trade(
    bars: Bars,
    symbol: str,
    d: dict,
    k: float,
    st: dict,
    *,
    rights=("C", "P"),
    em_dollars: float | None = None,
    readings: bool = False,
    spec: dict = SPEC,
) -> dict:
    """One position for one window at k x EM: priced at the verdict fill, the stress, mid and the
    natural fill, out at the judged bar; with `readings`, the reported bars too."""
    em = em_dollars if em_dollars is not None else st["em"]["expected_move_dollars"]
    spot, tol = st["spot"], spec["strike_tolerance_em"]
    call_k = put_k = None
    if "C" in rights:
        call_k, why = pick_strike(st["listed"]["C"], spot + k * em, spot, em, "C", tol)
        if why:
            return {"skip": why}
    if "P" in rights:
        put_k, why = pick_strike(st["listed"]["P"], spot - k * em, spot, em, "P", tol)
        if why:
            return {"skip": why}
    legs = legs_for(rights, call_k, put_k)
    entry_q = [st[role][(right, kk)] for _s, role, right, kk in legs]
    exits, adverse, exit_spot, why = exit_quotes(bars, symbol, d, legs, entry_q, "reaction", exit_bars(spec))
    if why:
        return {"skip": why}
    p = price(symbol, legs, entry_q, exits)
    if "skip" in p:
        return p
    out = {
        **p,
        "call_k": call_k,
        "put_k": put_k,
        "em": em,
        "adverse_legs": adverse,
        "days": max((d["reaction"] - d["entry"]).days, 1),
        "stress_ret": price(symbol, legs, entry_q, exits, stress=EVENT_RULE["stress"]).get("ret"),
        "mid_ret": price(symbol, legs, entry_q, exits, fill="mid").get("ret"),
        "natural_ret": price(symbol, legs, entry_q, exits, fill="natural").get("ret"),
        "move_em": (exit_spot - spot) / em,
        "entry_delta": 100.0
        * sum(s * (q.get("delta") or 0.0) for (s, *_), q in zip(legs, entry_q, strict=True)),
    }
    backs = [
        (q0, x)
        for (s, role, *_), q0, x in zip(legs, entry_q, exits, strict=True)
        if role == "back" and "quote" in x
    ]
    ivs = [(q0.get("iv"), x["quote"].get("iv")) for q0, x in backs]
    out["back_iv_change"] = _mean(b - a for a, b in ivs if a and b)
    if readings:
        for name, (session_key, bar) in spec["reported_bars"].items():
            xs, _a, _s, w = exit_quotes(bars, symbol, d, legs, entry_q, session_key, [bar])
            out[f"ret_{name}"] = None if w else price(symbol, legs, entry_q, xs).get("ret")
    return out


# --------------------------------------------------------------------------- the plan


def extended_events(names: list[str], host: str, port: int, after: date, through: date) -> list[dict]:
    """Item 31's `build_events` over the calendar's later rows: reports after `after` whose reaction
    session is on or before `through`."""
    rows = [r for r in e31.dolt_all_reports(names, host, port) if r[1] > after]
    events, _ = e31.build_events(rows)
    return [e for e in events if date.fromisoformat(e["reaction_session"]) <= through]


def build_plan(events: list[dict], reports: dict, span) -> tuple[list[dict], dict]:
    rows, dropped = [], Counter()
    for e in events:
        if e.get("early_close_entry"):
            dropped["early_close_entry"] += 1
            continue
        d = trade_dates(e)
        if d is None:
            dropped["no_expiry"] += 1
            continue
        row = {**e, "dates": {k: v.isoformat() for k, v in d.items()}, "control": None}
        c = e31.control_for(e, reports.get(e["symbol"], []), span)
        if c is None:
            dropped["no_control"] += 1
        else:
            cd = trade_dates(c)
            if cd is None or cal.is_early_close(cd["entry"]):
                dropped["control_unpriceable"] += 1
            else:
                row["control"] = {**c, "dates": {k: v.isoformat() for k, v in cd.items()}}
        rows.append(row)
    return rows, dict(dropped)


def _dates(x: dict) -> dict:
    return {k: date.fromisoformat(v) for k, v in x.items()}


def requests_of(plan: list[dict]) -> list[dict]:
    """Every request the plan reads, newest events first, each event's control after it; each once."""
    out, seen = [], set()
    for row in sorted(plan, key=lambda r: (r["entry_session"], r["symbol"]), reverse=True):
        for who, nxt in ((row, True), (row.get("control"), False)):
            if not who:
                continue
            for r in requests_for(row["symbol"], _dates(who["dates"]), with_next=nxt):
                k = (r["symbol"], r["session"], r["expiration"], r["start"], r["end"], r["interval"])
                if k not in seen:
                    seen.add(k)
                    out.append(r)
    return out


def planned_mde(b1: list[dict], planned_design: int) -> dict | None:
    """The MDE line from item 52's B1 design trades: the paired return (net less the control's, over
    the debit) as the past spread, this item's planned design events as n."""
    vals = [
        (t["net"] - t["control_net"]) / abs(t["debit"])
        for t in b1
        if t.get("arm") == "B1"
        and t.get("window") == "design"
        and t.get("control_net") is not None
        and t.get("debit")
    ]
    if len(vals) < 2 or planned_design < 2:
        return None
    return robustness.planned_mde(vals, planned_design, paired=True)


def cmd_plan(args) -> int:
    src = results_dir("item31")
    events = json.loads((src / "events.json").read_text(encoding="utf-8"))["events"]
    names = sorted({e["symbol"] for e in events})
    through = date.fromisoformat(SPEC["extend_reaction_through"])
    events = events + extended_events(names, args.host, args.port, e31.LAST_REPORT, through)
    cal_doc = e31.calendar_document(e31.dolt_all_reports(names, args.host, args.port))
    reports, span = e31.reports_from_document(cal_doc)
    plan, dropped = build_plan(events, reports, span)
    out = results_dir()
    (out / "calendar.json").write_text(json.dumps(cal_doc), encoding="utf-8")
    reqs = requests_of(plan)
    design_n = sum(1 for r in plan if part_of(r["entry_session"]) == "design")
    b1_path = results_dir("item52") / "trades.json"
    b1 = json.loads(b1_path.read_text(encoding="utf-8")) if b1_path.exists() else []
    mde = planned_mde(b1, design_n)
    (out / "plan.json").write_text(
        json.dumps(
            {"events": plan, "dropped": dropped, "planned_mde": mde, "planned_design": design_n}, indent=1
        ),
        encoding="utf-8",
    )
    path = Path(args.out) if args.out else out / "intraday_requests.json"
    path.write_text(json.dumps({"requests": reqs}, indent=1), encoding="utf-8")
    sample = out / "intraday_sample.json"
    sample.write_text(json.dumps({"requests": reqs[: SPEC["sample_requests"]]}, indent=1), encoding="utf-8")
    parts = Counter(part_of(r["entry_session"]) for r in plan)
    print(json.dumps({"written": str(path), "events": len(plan), "parts": dict(parts),
                      "requests": len(reqs), "dropped": dropped}))  # fmt: skip
    return 0


# --------------------------------------------------------------------------- the study


_BAR_QUOTED = (
    "SELECT 1 FROM intraday_chain_quotes WHERE symbol = ? AND session = ? AND expiration = ? AND bar = ? "
    "AND bid IS NOT NULL AND ask IS NOT NULL AND bid > 0 AND iv IS NOT NULL AND iv > 0 LIMIT 1"
)


def request_state(conn: sqlite3.Connection, req: dict) -> str:
    """What became of one request: `complete` when the store answered it and its required bar holds a
    quoted, priced contract; else `incomplete`, `empty`, `refused` or `unanswered`. Unlike item 31's
    pilot test it does not ask for the underlying, which the current tier stores only on an expiry's
    own day (the study takes the parity forward instead)."""
    sym, day, exp = req["symbol"].upper(), req["session"], req["expiration"]
    row = conn.execute(
        "SELECT status, detail FROM intraday_chain_requests WHERE symbol = ? AND session = ? "
        'AND expiration = ? AND start = ? AND "end" = ? AND interval = ?',
        (sym, day, exp, req["start"], req["end"], req["interval"]),
    ).fetchone()
    if row is None:
        return "unanswered"
    status, detail = row
    if status == "refused":
        return "unanswered" if (detail or "").split(":")[0] in e31.NOT_AN_ANSWER else "refused"
    if status == "empty":
        return "empty"
    hit = all(conn.execute(_BAR_QUOTED, (sym, day, exp, b)).fetchone() for b in req["required"])
    return "complete" if hit else "incomplete"


def coverage(conn: sqlite3.Connection, reqs: list[dict]) -> dict:
    """By entry year, events and controls together: the share of requests answered complete."""
    years: dict[int, Counter] = defaultdict(Counter)
    for r in reqs:
        y = years[r["year"]]
        y["requests"] += 1
        y[request_state(conn, r)] += 1
    return {
        "years": {
            str(y): {
                **dict(c),
                "share_complete": round(c["complete"] / c["requests"], 4) if c["requests"] else None,
            }
            for y, c in sorted(years.items())
        }
    }


def run(bars: Bars, plan: list[dict], years: set[int] | None, reporter=None) -> dict:
    trades = {a: [] for a in ARMS}
    reported = defaultdict(list)
    skips: Counter = Counter()
    unjudged: Counter = Counter()
    events = plan
    if reporter is not None:
        events = progress.track(
            plan,
            job="earnings_double_calendar_item99-study",
            phase="events",
            unit="events",
            key=lambda e: e["symbol"],
            current=lambda e: e["entry_session"],
            reporter=reporter,
        )
    for e in events:
        sym = e["symbol"]
        d = _dates(e["dates"])
        st, why = setup(bars, sym, d)
        if why:
            skips[why] += 1
            continue
        cst = cd = None
        if e.get("control"):
            cd = _dates(e["control"]["dates"])
            cst, cwhy = setup(bars, sym, cd)
            if cwhy:
                skips["control_" + cwhy] += 1
        in_judged = years is None or d["entry"].year in years
        base = {
            "symbol": sym,
            "entry": e["entry_session"],
            "report": e["report_date"],
            "part": part_of(e["entry_session"]),
            "em_method": st["em"]["expected_move_method"],
        }
        for arm, k in {**ARMS, **NEIGHBOURS}.items():
            t = window_trade(bars, sym, d, k, st, readings=arm in ARMS)
            if "skip" in t:
                skips[f"{arm}_{t['skip']}"] += 1
                continue
            t.update(base, arm=arm, control_ret=None)
            if cst is not None:
                c = window_trade(bars, sym, cd, k, cst)
                if "skip" in c:
                    skips[f"{arm}_control_{c['skip']}"] += 1
                else:
                    t["control_ret"] = c["ret"]
            if arm in ARMS:
                for side in ("C", "P"):
                    s = window_trade(bars, sym, d, k, st, rights=(side,))
                    t[f"ret_{side.lower()}_only"] = s.get("ret")
                if not in_judged:
                    unjudged[arm] += 1
                    reported["unjudged_" + arm].append(t)
                else:
                    trades[arm].append(t)
            else:
                reported[arm].append(t)
        straddle_em = st["em"].get("expected_move_straddle")
        if straddle_em:
            pb = window_trade(bars, sym, d, 1.0, st, em_dollars=straddle_em)
            if "skip" not in pb:
                reported["paper_book_strikes"].append({**pb, **base})
    return {"trades": trades, "reported": dict(reported), "skips": dict(skips), "unjudged": dict(unjudged)}


def summary(ts: list[dict], key: str = "ret") -> dict:
    vals = [t.get(key) for t in ts if t.get(key) is not None]
    if not vals:
        return {"trades": 0}
    return {"trades": len(vals), "mean": _mean(vals), "win_rate": sum(v > 0 for v in vals) / len(vals)}


def reported_block(res: dict) -> dict:
    out: dict = {"arms": {}, "neighbours": {}}
    for arm, ts in res["trades"].items():
        design = [t for t in ts if t["part"] == "design"]
        out["arms"][arm] = {
            "by_move": {
                b: summary([t for t in design if move_bucket(t["move_em"]) == b])
                for b in ("under_0.5", "0.5_to_1", "over_1")
            },
            "fills": {f: summary(design, f) for f in ("ret", "mid_ret", "natural_ret", "stress_ret")},
            "readings": {n: summary(design, f"ret_{n}") for n in SPEC["reported_bars"]},
            "call_only": summary(design, "ret_c_only"),
            "put_only": summary(design, "ret_p_only"),
            "back_iv_change": _mean(t.get("back_iv_change") for t in design),
            "entry_delta": _mean(t.get("entry_delta") for t in design),
            "adverse_legs": sum(t["adverse_legs"] for t in ts),
            "em_methods": dict(Counter(t["em_method"] for t in ts)),
            "years": {
                y: summary([t for t in ts if t["entry"][:4] == y])
                for y in sorted({t["entry"][:4] for t in ts})
            },
            "name_halves": {
                h: summary([t for t in design if robustness.name_half(t["symbol"]) == h]) for h in (0, 1)
            },
        }
    for arm in NEIGHBOURS:
        out["neighbours"][arm] = summary([t for t in res["reported"].get(arm, []) if t["part"] == "design"])
    out["paper_book_strikes"] = summary(
        [t for t in res["reported"].get("paper_book_strikes", []) if t["part"] == "design"]
    )
    out["skips"] = res["skips"]
    out["unjudged_years_trades"] = res["unjudged"]
    return out


def rate_reader():
    seen: dict[str, float] = {}

    def rate(session: str) -> float:
        if session not in seen:
            got = treasury.yield_before(session, "3m")
            if got is None or (date.fromisoformat(session) - got[0]).days > RATE_STALE_DAYS:
                raise SystemExit(f"no Treasury 3-month yield within {RATE_STALE_DAYS} days before {session}")
            seen[session] = got[1] / 100.0
        return seen[session]

    return rate


def cmd_study(args) -> int:
    out = results_dir()
    plan_doc = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    req_file = out / ("intraday_sample.json" if args.smoke else "intraday_requests.json")
    reqs = json.loads(req_file.read_text(encoding="utf-8"))["requests"]
    conn = intraday_store()
    try:
        cov = coverage(conn, reqs)
        plan = plan_doc["events"]
        if args.smoke:
            asked = {(r["symbol"], r["session"]) for r in reqs if r["kind"] == "entry"}
            plan = [e for e in plan if (e["symbol"], e["entry_session"]) in asked]
        years = None if args.smoke else judged_years(cov, EVENT_RULE["coverage_floor"])
        bars = Bars(conn)
        with progress.Reporter("earnings_double_calendar_item99-study") as rep:
            res = run(bars, plan, years, reporter=rep)
    finally:
        conn.close()
    if args.smoke:
        body = {"smoke": True, "verdict": "smoke", "events_read": len(plan),
                "trades": {a: len(ts) for a, ts in res["trades"].items()}, "skips": res["skips"]}  # fmt: skip
        name = "smoke.json"
    else:
        body = {**judge(res["trades"], rate_reader()), "reported": reported_block(res),
                "judged_years": sorted(years)}  # fmt: skip
        name = "results.json"
        (out / "trades.json").write_text(json.dumps(res["trades"], default=str), encoding="utf-8")
    doc = {
        "licence": "derived from licensed vendor data: never commit or publish",
        "item": 99,
        "spec": SPEC,
        "event_rule": EVENT_RULE,
        "window": "design before 2024-01-01; holdout 2024-01-01..2026-07-14; confirmation 2026-07-15 on",
        "plan_dropped": plan_doc["dropped"],
        "planned_mde": plan_doc.get("planned_mde"),
        "coverage": cov,
        **body,
        "corrections": list(REPLAY_CORRECTIONS),
    }
    path = Path(args.out) if args.out else out / name
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": str(path), "verdict": doc["verdict"]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="step", required=True)
    for name, fn in (("plan", cmd_plan), ("study", cmd_study)):
        p = sub.add_parser(name)
        p.add_argument("--out", default=None)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=3306)
        if name == "study":
            p.add_argument("--smoke", action="store_true", help="the sample fetched so far: counts only")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
