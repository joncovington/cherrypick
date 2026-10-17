"""Replay the earnings-navigator spread over the earnings events in the local Dolt clones.

    python scripts/earnings_navigator_history.py [--start 2020-01-01] [--end 2026-10-02] [--workers 8]
        [--cache FILE] [--cutoffs FILE] [--json FILE] [--limit N] [--symbols A,B]
    python scripts/earnings_navigator_history.py --from-json FILE

Step 1 of docs/earnings-screener-plan.md: does the rule make money after costs, and which of its
unsettled details matter? Read-only throughout; nothing is written except the optional cache and
JSON files.

**The rule.** A name with a strong direction read reports. If its IV rank is under the cut, buy a
debit spread in the direction (calls for a bull, puts for a bear); otherwise sell a credit spread on
the other side (puts for a bull, calls for a bear). Every event is priced both ways, so the cut is
chosen in the analysis, not the replay.

**Direction** is Bearing (`packages/technicals` `compass.py`), fitted on the captured security lists
and read from our own bars ONE session before the entry close. That is how the lists turned out to
be dated (the plan's gap 4: the trend inputs agree on 92-98% that way). The 1-10 score uses the
whole-market cut-offs round 5 builds (`round5._market`), cached with `--cutoffs`. A "bull"/"bear"
read, the tier below strong, is priced too, for the strong-plus variant.

**Universe.** There is no liquidity-rank history, so "very liquid" is stood in for by the EM-fly
study's screen: $100M a day in the stock, price at least $10, and an ATM straddle no wider than 15%
of its mid on the event expiry. Only events whose calendar row says before or after the open.

**Entry** at the last close before the report (the report day for after the close, the day before
for before the open). The chain is the one on that day; up to two days earlier is allowed and
counted (`entry_lag`), and the headline uses lag 0 only.

**The expected move** is the suite's definition, 0.85 x the ATM straddle mid on the first expiry on
or after the reaction day (within 17 days), as the EM-fly study and the earnings module take it.

**Trade expiries:** `event` that same expiry; `d30` the listed expiry nearest 30 days (21-45);
`d45` nearest 45 (30-60); `cycle` the user's standard monthly (45-60 days, else 30-60). Dolt lists
few expirations a day, so each is counted where it exists.

**Legs** (strikes as listed on the entry day; `s` is +1 for a bull, -1 for a bear):
- debit, long `atm` (nearest the price) or `d40` (nearest 0.40 delta); short the listed strike
  nearest price + s x EM.
- credit `user`: short ATM, long the first listed strike beyond price - s x EM (the user's rule,
  2026-10-16). `user_raw`: the same beyond the raw straddle (EM / 0.85). `v2025`: short ATM, long
  nearest 0.25 delta. `v2026`: short the first strike beyond the EM, long the first beyond 1.5 x EM
  (our reading of "buy further out").
Each is also priced in the opposite direction (`against`), the control on whether the direction
read is worth anything.

**Exits:** `react` the reaction day's close (the next chain day with the expiry listed, within 3
days; exact only on the day itself); `day2` the next chain day after that, within 4; `expiry` held
to the close, intrinsic, $5 per in-the-money strike. A leg the exit day did not list is priced from
that day's smile (`premium_cycle_replay.Slice`). The headline (`exact`) takes trades whose entry and
exit fall on the right days, with any unlisted strike read off that day's smile. `strict` also
demands both legs quoted, and it is BIASED: after a big move the legs sit far from the money, where
Dolt quotes fewest strikes, so it drops the big losers (in the eight-name smoke run, all four trades
it dropped had moved more than the expected move against the read). It is printed as a check only.

**Costs** as the EM-fly study: `cherrypick.core.fees` entry and exit costs with the suite's defaults
on a two-leg order, fills at mid with slippage charged as a cost. Net is whole-position dollars for
one spread; risk is the debit (or the width less the credit) plus entry costs.
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from cherrypick.core import fees as _fees
from cherrypick.core.structures import STRADDLE_TO_EM_FACTOR
from cherrypick.technicals import compass, history, levels, paths, round5, store, study, trend

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_stock_universe as _bsu  # noqa: E402 -- the monthly-expiry rule, shared not copied
import emfly_history as _em  # noqa: E402 -- the chain cache, bars and smile, shared not copied

MULT = 100
SETTLEMENT_PER_ITM_STRIKE = 5.00
SPREAD_ORDER = {"order": {"legs": [{"quantity": 1}, {"quantity": 1}]}}
LATE = date(2025, 1, 1)  # daily chain snapshots from here
EXPIRIES = ("event", "d30", "d45", "cycle")
DEBIT_LONGS = ("atm", "d40")
CREDIT_LEGS = ("user", "user_raw", "v2025", "v2026")
EXITS = ("react", "day2", "expiry")
TIERS = {"strong": {"strong bull", "strong bear"}, "strong+": {"strong bull", "strong bear", "bull", "bear"}}
BULLISH = {"strong bull", "bull"}
CUTS = (33, 45, 50)
DEFAULTS = {
    "src": "entry",
    "cut": 50,
    "exp": "cycle",
    "debit": "atm",
    "credit": "user",
    "exit": "react",
    "tier": "strong",
}


# --------------------------------------------------------------------------- inputs


def market_cutoffs(workers: int, cache: Path | None) -> dict[str, list[float]]:
    """{session: the nine decile cut-offs}, built as round 5 builds them; cached as JSON."""
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    path = paths.history_db()
    conn = history.connect(path)
    data_end = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    everyone = [s for (s,) in conn.execute("SELECT symbol FROM peaks ORDER BY symbol")]
    dates = [b.date for b in store.adjusted_bars(conn, "SPY")]
    conn.close()
    print(f"cut-offs: market scores over {len(everyone):,} names", file=sys.stderr)
    by_day: dict[int, list[float]] = defaultdict(list)
    with ProcessPoolExecutor(
        max_workers=workers, initializer=round5._init, initargs=(str(path), data_end)
    ) as pool:
        for part in pool.map(round5._market, study._chunks(everyone, workers * 8)):
            for k, s in part:
                by_day[k].append(s)
    out = {dates[k]: levels.rank_cutoffs(v) for k, v in by_day.items() if len(v) >= 100}
    if cache:
        cache.write_text(json.dumps(out), encoding="utf-8")
    return out


def load_ivr(
    host: str, port: int, symbols: list[str], start: date, cache: Path | None = None
) -> dict[str, list[tuple[date, float]]]:
    """symbol -> [(date, IV rank 0-100)], Dolt's 30-day IV against its own one-year range. With
    `cache`, names already in it are read from it and names fetched are added, so a re-run only
    queries what is new (the query is the study's slowest step: about two minutes a 60-name batch)."""
    out: dict[str, list] = defaultdict(list)
    if cache and cache.exists():
        for s, rows in json.loads(cache.read_text(encoding="utf-8")).items():
            out[s] = [(date.fromisoformat(d), v) for d, v in rows]
    todo = [s for s in symbols if s not in out]
    cur = _em._dolt(host, port).cursor()
    for i in range(0, len(todo), 60):
        part = todo[i : i + 60]
        cur.execute(
            "SELECT act_symbol, date, iv_current, iv_year_high, iv_year_low FROM options.volatility_history "
            f"WHERE date >= %s AND act_symbol IN ({','.join(['%s'] * len(part))})",
            [start - timedelta(days=60), *part],
        )
        for s, d, c, hi, lo in cur.fetchall():
            if c is not None and hi is not None and lo is not None and float(hi) > float(lo):
                out[s].append((d, 100.0 * (float(c) - float(lo)) / (float(hi) - float(lo))))
    cur.close()
    for s in todo:
        out.setdefault(s, [])  # a name Dolt holds no IV for is cached as empty, not re-queried
    for v in out.values():
        v.sort()
    if cache and todo:
        body = {s: [(d.isoformat(), round(v, 3)) for d, v in rows] for s, rows in out.items()}
        cache.write_text(json.dumps(body), encoding="utf-8")
    return out


def ivr_on(series: list[tuple[date, float]], d: date) -> float | None:
    """The latest reading on or up to four days before `d`."""
    j = bisect.bisect_right(series, (d, float("inf"))) - 1
    if j < 0 or (d - series[j][0]).days > 4:
        return None
    return round(series[j][1], 1)


# --------------------------------------------------------------------------- pure pieces


def nearest(ks: list[float], target: float) -> float | None:
    return min(ks, key=lambda k: (abs(k - target), k)) if ks else None


def beyond(ks: list[float], level: float, down: bool) -> float | None:
    """The first listed strike at or past `level`: below it when `down`, else above."""
    if down:
        below = [k for k in ks if k <= level]
        return max(below) if below else None
    above = [k for k in ks if k >= level]
    return min(above) if above else None


def by_delta(q: dict, target: float) -> float | None:
    best = None
    for k, (_, _, _, dl) in q.items():
        if dl is None:
            continue
        gap = abs(abs(dl) - target)
        if best is None or gap < best[0]:
            best = (gap, k)
    return None if best is None or best[0] > 0.1 else best[1]


def pick_expiries(exps: list[str], entry: date, front: str) -> dict[str, str]:
    def dte(e):
        return (date.fromisoformat(e) - entry).days

    out = {"event": front}
    for name, target, lo, hi in (("d30", 30, 21, 45), ("d45", 45, 30, 60)):
        inside = [e for e in exps if lo <= dte(e) <= hi]
        if inside:
            out[name] = min(inside, key=lambda e: (abs(dte(e) - target), e))
    monthly = [e for e in exps if _bsu.is_monthly(e)]
    for lo, hi in ((45, 60), (30, 60)):
        inside = [e for e in monthly if lo <= dte(e) <= hi]
        if inside:
            out["cycle"] = max(inside, key=dte)
            break
    return out


def legs_for(q: dict, spot: float, em: float, bull: bool) -> dict[str, tuple[str, float, float]]:
    """{'debit|atm': (kind, long, short), 'credit|user': (kind, short, long), ...} on listed strikes.
    `q` maps each side ('C'/'P') to {strike: (bid, ask, iv, delta)}."""
    s = 1 if bull else -1
    out = {}
    cd = "C" if bull else "P"
    ks = sorted(q[cd])
    if ks:
        short = nearest(ks, spot + s * em)
        for name, long_ in (("atm", nearest(ks, spot)), ("d40", by_delta(q[cd], 0.40))):
            if long_ is not None and short is not None and (short - long_) * s > 0:
                out[f"debit|{name}"] = (cd, long_, short)
    cc = "P" if bull else "C"
    ks = sorted(q[cc])
    if ks:
        down = bull  # a bull sells puts: the protection sits below
        atm = nearest(ks, spot)
        cand = {
            "user": (atm, beyond(ks, spot - s * em, down)),
            "user_raw": (atm, beyond(ks, spot - s * em / STRADDLE_TO_EM_FACTOR, down)),
            "v2025": (atm, by_delta(q[cc], 0.25)),
        }
        sh26 = beyond(ks, spot - s * em, down)
        lg26 = beyond(ks, spot - s * 1.5 * em, down)
        if sh26 is not None and lg26 == sh26:
            further = [k for k in ks if (k < sh26 if down else k > sh26)]
            lg26 = (max(further) if down else min(further)) if further else None
        cand["v2026"] = (sh26, lg26)
        for name, (short, long_) in cand.items():
            # the long sits further from the money than the short: below it for puts, above for calls
            if short is not None and long_ is not None and (short - long_) * (1 if down else -1) > 0:
                out[f"credit|{name}"] = (cc, short, long_)
    return out


def leg_value(q_side: dict, k: float, sl, cp: str) -> tuple[float, dict, bool] | None:
    """(mid, {bid, ask}, listed?) for strike k: as listed, else from the day's smile."""
    hit = q_side.get(k)
    if hit is not None:
        b, a = hit[0], hit[1]
        return (b + a) / 2, {"bid": b, "ask": a}, True
    if sl is None:
        return None
    m, spr = sl.quote(cp, k)
    return m, {"bid": max(m - spr / 2, 0.0), "ask": m + spr / 2}, False


def spread_value(kind: str, a: float, b: float) -> float:
    """Debit: long (a) less short (b). Credit: short (a) less long (b). Either is what's held."""
    return a - b


def intrinsic(cp: str, k: float, s: float) -> float:
    return max(0.0, s - k) if cp == "C" else max(0.0, k - s)


def passes_screen(ev: dict, bars: dict, args) -> bool:
    """The entry close's price and 20-session dollar volume, as `build_event` screens them."""
    dates = bars["dates"]
    i = bisect.bisect_left(dates, ev["date"])
    ip = i if ev["amc"] else i - 1
    if ip < 19 or ip >= len(dates):
        return False
    if bars["raw"][ip] < args.min_price:
        return False
    return statistics.fmean(bars["dollar_volume"][ip - 19 : ip + 1]) >= args.min_dollar_volume


# --------------------------------------------------------------------------- one event


def build_event(ev, bars, ctx, chains, ivr, args) -> dict | None:
    dates = bars["dates"]
    d0 = ev["date"]
    i = bisect.bisect_left(dates, d0)
    if ev["amc"]:
        if i >= len(dates) or dates[i] != d0:
            ev["drop"] = "no_bar_on_date"
            return None
        ip, ir = i, i + 1
    else:
        ip, ir = i - 1, (i if i < len(dates) and dates[i] == d0 else None)
        if ir is None:
            ev["drop"] = "no_bar_on_date"
            return None
    if ip < 260 or ir >= len(dates):
        ev["drop"] = "short_history"
        return None
    P, R = dates[ip], dates[ir]
    spot = bars["raw"][ip]
    if spot < args.min_price:
        ev["drop"] = "price"
        return None
    if statistics.fmean(bars["dollar_volume"][ip - 19 : ip + 1]) < args.min_dollar_volume:
        ev["drop"] = "dollar_volume"
        return None

    # Direction, one session before the entry close.
    S = dates[ip - 1]
    adj = bars["adj"][:ip]
    s1, s6 = trend.last_score(adj, trend.SHORT_TERM), trend.last_score(adj, trend.LONG_TERM)
    k = ctx["idx"].get(S.isoformat())
    score = None
    if k is not None:
        sc = round5._score_on(bars["rawd"], bars["splits"], ctx["dates"], k)
        score = round5.decile(sc, ctx["cutoffs"].get(S.isoformat()))
    label = compass.bearing(ctx["lookup"], s1, s6, score)
    if label not in TIERS["strong+"]:
        ev["drop"] = "direction"
        return None
    bull = label in BULLISH

    c0, rows = None, []
    for back in range(0, 3):
        d = P - timedelta(days=back)
        rows = chains.day(ev["symbol"], d)
        if rows:
            c0 = d
            break
    if c0 is None:
        ev["drop"] = "no_entry_chain"
        return None
    if c0 != P:
        j = bisect.bisect_right(dates, c0) - 1
        if dates[j] != c0:
            ev["drop"] = "no_entry_chain"
            return None
        ip, spot = j, bars["raw"][j]
    exps = sorted({r[0] for r in rows if date.fromisoformat(r[0]) >= R})
    if not exps or (date.fromisoformat(exps[0]) - P).days > 17:
        ev["drop"] = "no_front_expiration"
        return None
    front = exps[0]
    calls, puts = _em.quotes_by_strike(rows, front, True), _em.quotes_by_strike(rows, front, False)
    both = sorted(set(calls) & set(puts))
    if not both:
        ev["drop"] = "no_atm"
        return None
    atm = min(both, key=lambda x: (abs(x - spot), x))
    straddle = _em.mid(calls[atm][0], calls[atm][1]) + _em.mid(puts[atm][0], puts[atm][1])
    spread = (calls[atm][1] - calls[atm][0]) + (puts[atm][1] - puts[atm][0])
    if straddle <= 0 or spread / straddle > args.max_straddle_spread:
        ev["drop"] = "straddle_spread"
        return None
    em = STRADDLE_TO_EM_FACTOR * straddle

    out = {
        "symbol": ev["symbol"],
        "date": d0.isoformat(),
        "amc": ev["amc"],
        "entry": c0.isoformat(),
        "entry_lag": (P - c0).days,
        "react_day": R.isoformat(),
        "spot": spot,
        "em": round(em, 4),
        "move": round(bars["raw"][ir] - bars["raw"][ip], 4),
        "label": label,
        "inputs": [s1, s6, score],
        "ivr_entry": ivr_on(ivr.get(ev["symbol"], []), S),
        "ivr_base": ivr_on(ivr.get(ev["symbol"], []), S - timedelta(days=35)),
        "trades": {},
    }

    # Exit days: the reaction day's chain (or the next, within 3 days), and the chain day after.
    marks = []
    d = R
    while d <= R + timedelta(days=10) and len(marks) < 2:
        j = bisect.bisect_left(dates, d)
        if d.weekday() < 5 and j < len(dates) and dates[j] == d:
            r = chains.day(ev["symbol"], d)
            if r:
                marks.append((d, r, bars["raw"][j]))
        d += timedelta(days=1)

    for xname, X in pick_expiries(exps, P, front).items():
        Xd = date.fromisoformat(X)
        q = {"C": _em.quotes_by_strike(rows, X, True), "P": _em.quotes_by_strike(rows, X, False)}
        ie = bisect.bisect_right(dates, Xd) - 1
        expiry_ok = dates[-1] >= Xd and ie >= ir
        if expiry_ok:
            ratio = [bars["raw"][x] / bars["adj"][x] for x in (ip, ie)]
            expiry_ok = abs(ratio[0] / ratio[1] - 1) <= 0.01
        for direction, is_bull in (("with", bull), ("against", not bull)):
            for name, (cp, a, b) in legs_for(q, spot, em, is_bull).items():
                kind = name.split("|")[0]
                qa, qb = q[cp][a], q[cp][b]
                va, vb = (qa[0] + qa[1]) / 2, (qb[0] + qb[1]) / 2
                entry_val = spread_value(kind, va, vb)
                width = abs(a - b)
                if entry_val <= 0.01 or entry_val >= width:
                    continue
                lq = [{"bid": qa[0], "ask": qa[1]}, {"bid": qb[0], "ask": qb[1]}]
                ecost = _fees.apply_entry_costs(SPREAD_ORDER, lq, 1, {})["total_cost"]
                risk = (entry_val if kind == "debit" else width - entry_val) * MULT + ecost
                res = {
                    "legs": [a, b],
                    "cp": cp,
                    "value": round(entry_val, 4),
                    "width": width,
                    "risk": round(risk, 2),
                    "dte": (Xd - P).days,
                }
                for ex, (md, mrows, mspot) in zip(("react", "day2"), marks, strict=False):
                    if ex == "react" and (md - R).days > 3:
                        continue
                    if not any(r[0] == X for r in mrows):
                        continue
                    mq = _em.quotes_by_strike(mrows, X, cp == "C")
                    sl = _em.make_slice(mrows, X, md, mspot)
                    la, lb = leg_value(mq, a, sl, cp), leg_value(mq, b, sl, cp)
                    if la is None or lb is None:
                        continue
                    exit_val = spread_value(kind, la[0], lb[0])
                    xcost = _fees.apply_exit_costs(SPREAD_ORDER, [la[1], lb[1]], 1, {})["total_cost"]
                    gross = (exit_val - entry_val) if kind == "debit" else (entry_val - exit_val)
                    res[ex] = round(gross * MULT - ecost - xcost, 2)
                    # exact: the right day (the reaction day itself for `react`); listed: both legs
                    # quoted that day rather than read off its smile
                    res[f"{ex}_exact"] = ex != "react" or md == R
                    res[f"{ex}_listed"] = bool(la[2] and lb[2])
                if expiry_ok and Xd >= R:
                    s_exp = bars["raw"][ie]
                    ia, ib = intrinsic(cp, a, s_exp), intrinsic(cp, b, s_exp)
                    itm = (ia > 0) + (ib > 0)
                    owed = spread_value(kind, ia, ib)
                    gross = (owed - entry_val) if kind == "debit" else (entry_val - owed)
                    res["expiry"] = round(gross * MULT - ecost - SETTLEMENT_PER_ITM_STRIKE * itm, 2)
                    res["expiry_exact"] = res["expiry_listed"] = True
                out["trades"][f"{direction}|{xname}|{name}"] = res
    if not out["trades"]:
        ev["drop"] = "no_spread"
        return None
    return out


# --------------------------------------------------------------------------- analysis


def summarize(nets: list[float], risks: list[float]) -> dict:
    if not nets:
        return {"n": 0}
    return {
        "n": len(nets),
        "mean": round(statistics.fmean(nets), 2),
        "total": round(sum(nets), 0),
        "win": round(sum(1 for x in nets if x > 0) / len(nets), 3),
        "ror": round(sum(nets) / sum(risks), 4) if sum(risks) else None,
    }


def chosen(e: dict, cfg: dict, direction: str = "with") -> dict | None:
    """The trade the rule picks for one event under `cfg`, or None when it cannot."""
    if e["label"] not in TIERS[cfg["tier"]]:
        return None
    ivr = e["ivr_entry"] if cfg["src"] == "entry" else e["ivr_base"]
    if ivr is None:
        return None
    name = f"debit|{cfg['debit']}" if ivr < cfg["cut"] else f"credit|{cfg['credit']}"
    return e["trades"].get(f"{direction}|{cfg['exp']}|{name}")


def run_cfg(events: list[dict], cfg: dict, mode: str) -> dict:
    """`mode`: lenient (any mark), exact (the entry and the exit on the right day; an unlisted strike
    read off that day's smile), strict (exact, and both legs quoted)."""
    exact, strict = mode in ("exact", "strict"), mode == "strict"
    out = {}
    for period, sel in (
        ("all", events),
        ("pre2025", [e for e in events if date.fromisoformat(e["date"]) < LATE]),
        ("2025+", [e for e in events if date.fromisoformat(e["date"]) >= LATE]),
    ):
        for direction in ("with", "against"):
            nets, risks = [], []
            for e in sel:
                if exact and e["entry_lag"]:
                    continue
                t = chosen(e, cfg, direction)
                ex = cfg["exit"]
                if t is None or t.get(ex) is None or (exact and not t.get(f"{ex}_exact")):
                    continue
                if strict and not t.get(f"{ex}_listed"):
                    continue
                nets.append(t[ex])
                risks.append(t["risk"])
            out[f"{period}|{direction}"] = summarize(nets, risks)
    return out


VARY = {
    "src": ("entry", "base"),
    "cut": CUTS + (0, 101),  # 0 = always credit, 101 = always debit
    "exp": EXPIRIES,
    "debit": DEBIT_LONGS,
    "credit": CREDIT_LEGS,
    "exit": EXITS,
    "tier": tuple(TIERS),
}


def analyse(events: list[dict], head: dict) -> dict:
    rep = dict(head)
    rep["events"] = len(events)
    labels = Counter(e["label"] for e in events)
    rep["labels"] = dict(labels)
    hit = {}
    for lab in ("strong bull", "strong bear", "bull", "bear"):
        sel = [e for e in events if e["label"] == lab]
        if sel:
            right = sum(1 for e in sel if (e["move"] > 0) == (lab in BULLISH))
            hit[lab] = [len(sel), round(right / len(sel), 3)]
    rep["direction_hit"] = hit
    ivs = [e["ivr_entry"] for e in events if e["ivr_entry"] is not None]
    rep["ivr_entry_share_ge"] = (
        {c: round(sum(1 for x in ivs if x >= c) / len(ivs), 3) for c in CUTS} if ivs else {}
    )
    rep["one_at_a_time"] = {}
    for key, options in VARY.items():
        for opt in options:
            cfg = dict(DEFAULTS, **{key: opt})
            rep["one_at_a_time"][f"{key}={opt}"] = {
                m: run_cfg(events, cfg, m) for m in ("exact", "strict", "lenient")
            }
    grid = {}
    for src in VARY["src"]:
        for cut in VARY["cut"]:
            for exp in EXPIRIES:
                for credit in CREDIT_LEGS:
                    for ex in EXITS:
                        cfg = dict(DEFAULTS, src=src, cut=cut, exp=exp, credit=credit, exit=ex)
                        r = run_cfg(events, cfg, "exact")
                        grid[f"{src}|{cut}|{exp}|{credit}|{ex}"] = {
                            p: r[f"{p}|with"] for p in ("all", "pre2025", "2025+")
                        } | {"against_all": r["all|against"]}
    rep["grid"] = grid
    return rep


def fmt(s: dict) -> str:
    if not s or not s.get("n"):
        return f"{'-':>32}"
    ror = f"{100 * s['ror']:+.1f}%" if s.get("ror") is not None else "-"
    return f"{s['n']:5d} {s['mean']:+8.2f} {100 * s['win']:5.1f}% {ror:>8}"


def print_report(rep: dict) -> None:
    print(f"\nevents priced: {rep['events']}   drops: {rep.get('drops')}")
    print(f"labels: {rep['labels']}")
    print("direction hit on the reaction (n, share moving the read's way):", rep["direction_hit"])
    print("IV rank on entry at or above each cut:", rep["ivr_entry_share_ge"])
    print("\nOne setting at a time around the defaults", DEFAULTS)
    print("cells: n, mean net $, win %, return on risk")
    print("exact: entry and exit on the right day (an unlisted strike read off that day's smile);")
    print("strict: exact with both legs quoted; lenient: any mark within the windows")
    print(f"{'setting':22s} {'all':>32} {'pre-2025':>32} {'2025 on':>32} {'against (all)':>32}")
    for name, r in rep["one_at_a_time"].items():
        for mode in ("exact", "strict", "lenient"):
            x = r[mode]
            cells = [fmt(x[c]) for c in ("all|with", "pre2025|with", "2025+|with", "all|against")]
            label = name if mode == "exact" else f"  {mode}"
            print(f"{label:22s} " + " ".join(cells))
    best = sorted(
        (
            (k, v)
            for k, v in rep["grid"].items()
            if v["all"].get("n", 0) >= 100 and v["all"].get("ror") is not None
        ),
        key=lambda kv: kv[1]["all"]["ror"],
        reverse=True,
    )
    print("\nGrid (src|cut|expiry|credit legs|exit), at least 100 exact trades,")
    print("best and worst five by return on risk; columns: all | pre-2025 | 2025 on | against (all)")
    for k, v in best[:5] + [("...", None)] + best[-5:]:
        if v is None:
            print("  ...")
            continue
        cells = [fmt(v[c]) for c in ("all", "pre2025", "2025+", "against_all")]
        print(f"  {k:36s} " + " | ".join(cells))


# --------------------------------------------------------------------------- driver


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-10-02")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--min-dollar-volume", type=float, default=100e6)
    ap.add_argument("--min-price", type=float, default=10.0)
    ap.add_argument("--max-straddle-spread", type=float, default=0.15)
    ap.add_argument(
        "--cache", type=Path, help="the chain cache (JSON lines), shared format with emfly_history"
    )
    ap.add_argument("--cutoffs", type=Path, help="cache for the whole-market score cut-offs")
    ap.add_argument("--ivr-cache", type=Path, help="cache for Dolt's IV rank history, per name")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--symbols", help="comma-separated, for a quick run")
    ap.add_argument("--from-json", type=Path, help="re-analyse the events a previous --json run saved")
    args = ap.parse_args(argv)
    if args.from_json:
        saved = json.loads(args.from_json.read_text(encoding="utf-8"))
        report = analyse(saved["events"], {k: saved["report"][k] for k in ("args", "drops")})
        print_report(report)
        if args.json:
            args.json.write_text(
                json.dumps({"report": report, "events": saved["events"]}, default=str), encoding="utf-8"
            )
        return 0

    started = time.monotonic()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    cutoffs = market_cutoffs(args.workers, args.cutoffs)
    snaps = compass.load_snapshots()
    lookup = compass.fit([r for rows in snaps.values() for r in rows])
    hconn = history.connect(paths.history_db())
    spy_dates = [b.date for b in store.adjusted_bars(hconn, "SPY")]
    ctx = {
        "cutoffs": cutoffs,
        "lookup": lookup,
        "dates": spy_dates,
        "idx": {d: k for k, d in enumerate(spy_dates)},
    }
    print(f"cut-offs for {len(cutoffs)} sessions; Bearing fitted on {sorted(snaps)}", file=sys.stderr)

    syms_opt = _em.option_symbols(args.host, args.port, start, end)
    events = [e for e in _em.load_events(args.host, args.port, start, end) if e["symbol"] in syms_opt]
    if args.symbols:
        keep = set(args.symbols.split(","))
        events = [e for e in events if e["symbol"] in keep]
    by_sym: dict[str, list] = defaultdict(list)
    for e in events:
        by_sym[e["symbol"]].append(e)
    bars_db = _em.Bars(_em.HISTORY_DB)
    series: dict[str, dict] = {}
    for s in sorted(by_sym):
        b = bars_db.series(s)
        if b is None:
            continue
        # The price and dollar-volume screen first (build_event applies it again exactly): most
        # reporters are small names it drops, and Dolt's IV history is slow to load per name.
        by_sym[s] = [e for e in by_sym[s] if passes_screen(e, b, args)]
        if not by_sym[s]:
            continue
        b["rawd"] = {r.date: r.close for r in store.raw_bars(hconn, s)}
        b["splits"] = [x.ex_date for x in store.splits(hconn, s)]
        series[s] = b
    print(f"names passing the price and volume screen: {len(series)}", file=sys.stderr)
    ivr = load_ivr(args.host, args.port, sorted(series), start, args.ivr_cache)
    chains = _em.Chains(args.host, args.port, args.cache)

    todo = sorted((e for s in series for e in by_sym[s]), key=lambda e: (e["date"], e["symbol"]))
    if args.limit:
        todo = todo[: args.limit]
    print(f"events to consider: {len(todo)}", file=sys.stderr)
    results: list[dict] = []
    drops: Counter = Counter()

    def work(e):
        try:
            return build_event(e, series[e["symbol"]], ctx, chains, ivr, args)
        except Exception as exc:  # noqa: BLE001 -- one bad row is a drop, never the run
            e["drop"] = f"error:{type(exc).__name__}"
            return None

    with ThreadPoolExecutor(args.workers) as pool:
        for n, (e, r) in enumerate(zip(todo, pool.map(work, todo), strict=True), 1):
            if r is None:
                drops[e.get("drop", "unknown")] += 1
            else:
                results.append(r)
            if n % 500 == 0:
                chains.flush()
                print(
                    f"  {n}/{len(todo)} considered, {len(results)} priced, {time.monotonic() - started:.0f}s",
                    file=sys.stderr,
                )
    chains.flush()
    head = {"args": {k: str(v) for k, v in vars(args).items()}, "drops": dict(drops)}
    report = analyse(results, head)
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps({"report": report, "events": results}, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
