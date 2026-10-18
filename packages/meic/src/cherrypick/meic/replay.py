"""The quote-history replay: a recorded session run through MEIC's own engine, tick by tick.

docs/quote-history-replay-plan.md, Phase 2. Each recorded sample of SPX's 0DTE chain (the gex
recorder's 15 s history from 2026-10-13, or any file in its schema -- packages/backtest's vendor
stores) is rebuilt as the snapshot `paper_loop.run_iteration` would have built at that instant and
handed to `paper.process_symbol` under `paper.pinned_clock`, so the rules that run are the paper
engine's own and the only thing written is a scratch ledger. Its own entry point, not a `cli` verb:
it writes (the meic CLI is read-only by test).

How each snapshot field is rebuilt (the plan's gap-2 rule: by a declared rule, or the tick is
refused, never approximated silently):

- **Chain**: the sample's quotes as `tt get_option_chain` rows, through
  `paper_loop.candidates_from_chain`, the strike selection the loop runs. The recorder keeps about 20
  strikes either side of spot where the live chain asks for 80, so a short or wing beyond that window
  is MEIC's own "no candidate", and an open leg that leaves it holds `quotes_unavailable` exactly as
  an unquoted leg does live.
- **Spot**: the sample's (`core.quotehistory`, joined at or before within 60 s).
- **GEX**: `core.quotehistory.RecordedDay.gex`, the recorder's per-strike profile at or before the
  sample through `core.gex`, refused past 360 s. Live recomputes it every tick from the stream
  cache; the profile moves in ~5-minute steps. `net_gex_vol` is not rebuilt (None), and nothing on
  the paper path gates on it.
- **Session**: day open, high and low from the spot trail up to the sample; `intraday_range_pct` is
  that range over spot. `atr_5day` is the mean true range of the prior `regime_atr_lookback_days`
  completed sessions in the same file (their trail's high and low, the close before each), None with
  fewer, as `tt get_atr` is. Live reads the streamer's exchange summary rows; a sampled trail is close
  to them, not identical.
- **VIX** (amended 2026-10-18): at or before each tick from the history file's own
  `market_regime_history`, through `core.regime.markets_at` and its staleness rule -- the recorder's
  live readings, or the vendor store's minute series from VIX options' underlying price
  (`backtest fetch-vix`). Paper moves its delta band the moment VIX crosses 18, 25 or 35, and a day's
  range straddled one of those on about one design-window session in five; a VIX fixed for the day
  could not follow (2026-10-16 matched 58% of paper's entries on the first rule, the other recorder
  days 95-97%). Where the file holds no reading in bounds, the session's Cboe VIX OPEN is the declared
  fallback, and each snapshot says which it used (`vix_source`; counted per run).
- **VIX1D and IV rank** (nothing intraday for VIX1D, which has no options): VIX1D is Cboe's daily file
  (`market-files/cboe`, `scripts/fetch_market_files.py`) at the session's open, as a ratio to the
  VIX open. IV rank is the tick's VIX against the range of the prior 252 Cboe closes and IV
  percentile the share of those closes below it, tagged `iv_rank_source = "vix_proxy"`: SPX's 30-day
  implied vol standing in for tastytrade's symbol IV rank. A session without a Cboe VIX open is
  refused whole (live falls back to a different delta band without VIX; a replay must not).

Refused: `advised:*` arms (a day's advice artifact decides them, and the replay has none), an arm
that never trades SPX, an existing `--out`.

`check` (`replay_check`) validates it against MEIC paper on the recorder era.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from cherrypick.core import home as _home
from cherrypick.core import quotehistory
from cherrypick.core.clock import ET

from cherrypick.meic import db as meic_db
from cherrypick.meic import paper, paper_loop

SYMBOL = "SPX"
#: The first session the gex recorder kept quotes for.
FIRST_SESSION = "2026-10-13"
IV_RANK_LOOKBACK = 252
VOL_SOURCE = "cboe_daily_open"
SETTLEMENT_TIME = "16:00"
#: The paper loop's tick (the supervisor fires `meic-paper` every 60 s). A replay ticks at the same
#: cadence whatever the history's own: on the recorder's 15 s file it takes one sample a minute, or
#: an arm without spacing (control) would enter four times as often as paper ever could.
DEFAULT_TICK_SECONDS = 60.0


def ticks(samples: list[dict], tick_seconds: float) -> list[dict]:
    """The samples a loop ticking every `tick_seconds` would have seen: the first, then each one at
    least `tick_seconds` after the last taken (a small tolerance absorbs the recorder's jitter)."""
    out, last = [], None
    for s in samples:
        if last is None or s["ts"] - last >= tick_seconds - 2.0:
            out.append(s)
            last = s["ts"]
    return out


def cboe_path(name: str) -> Path:
    return _home.data_dir("market-files") / "cboe" / f"{name}.csv"


def load_cboe(name: str) -> list[tuple[str, float, float]]:
    """[(session, open, close)] oldest first from a Cboe daily OHLC file; [] when absent. Rows that do
    not parse, and zero values, are dropped."""
    path = cboe_path(name)
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                day = datetime.strptime(row["DATE"].strip(), "%m/%d/%Y").date().isoformat()
                close = float(row.get("CLOSE") or 0)
                opened = float(row.get("OPEN") or 0) or close
            except (KeyError, ValueError):
                continue
            if close > 0 and opened > 0:
                out.append((day, opened, close))
    out.sort()
    return out


def iv_rank_of(value: float, prior: list[float]) -> tuple[float, float | None]:
    """(IV rank, IV percentile) of a VIX `value` against the prior sessions' closes: its place in their
    range (with itself), and the share of them below it."""
    window = [*prior, value]
    lo, hi = min(window), max(window)
    rank = round((value - lo) / (hi - lo), 3) if hi > lo else 0.5
    pct = round(sum(1 for c in prior if c < value) / len(prior), 3) if prior else None
    return rank, pct


def vol_inputs(day: str, vix: list[tuple[str, float, float]], vix1d: list[tuple[str, float, float]]) -> dict:
    """The day's VIX inputs from Cboe's daily files: the session's VIX open (the fallback where the
    history holds no intraday VIX), the prior `IV_RANK_LOOKBACK` closes the IV rank is read against,
    and the VIX1D/VIX ratio at the open. `{}` when the day has no VIX open."""
    days = [d for d, _, _ in vix]
    i = bisect.bisect_left(days, day)
    if i >= len(days) or days[i] != day:
        return {}
    vix_open = vix[i][1]
    prior = [c for _, _, c in vix[max(0, i - IV_RANK_LOOKBACK) : i]]
    iv_rank, iv_pct = iv_rank_of(vix_open, prior)
    v1 = {d: o for d, o, _ in vix1d}.get(day)
    return {
        "vix": vix_open,
        "prior_closes": prior,
        "vix1d_ratio": round(v1 / vix_open, 3) if v1 else None,
        "iv_rank": iv_rank,
        "iv_pct": iv_pct,
        "iv_rank_source": "vix_proxy",
        "vol_source": VOL_SOURCE,
    }


def intraday_vix(history_db: Path, stamps: list[float]) -> dict[float, float]:
    """VIX at or before each stamp from the history file's `market_regime_history` (the recorder's own
    readings, or the vendor store's from VIX options), through `core.regime.markets_at`, its staleness
    rule included. Stamps with no usable reading are absent."""
    from cherrypick.core import regime  # function-local: only the replay reads the regime series

    out = {}
    for ts, block in zip(stamps, regime.markets_at(stamps, history_db=history_db), strict=True):
        vix = (block.get("readings") or {}).get("vix") or {}
        if block.get("status") == "measured" and vix.get("usable") and vix.get("value"):
            out[ts] = float(vix["value"])
    return out


def prior_true_ranges(history_db: Path, symbol: str, day: str, sessions: int) -> list[float]:
    """True range of each of the `sessions` completed sessions before `day` in the file: the trail's
    high and low, against the close before it (`tt._true_ranges`' rule: a missing prior close degrades
    to the plain range)."""
    from cherrypick.core import db as core_db  # function-local: only the replay reads prior sessions

    if not Path(history_db).exists():
        return []
    conn = core_db.connect_ro(history_db)
    try:
        rows = conn.execute(
            "SELECT trade_date, MAX(spot) AS hi, MIN(spot) AS lo FROM gex_spot_history WHERE symbol = ? "
            "AND trade_date < ? AND spot IS NOT NULL GROUP BY trade_date ORDER BY trade_date DESC LIMIT ?",
            (symbol, day, sessions),
        ).fetchall()
        out = []
        for r in rows:
            prev = conn.execute(
                "SELECT close FROM daily_closes WHERE symbol = ? AND trade_date < ? ORDER BY trade_date DESC LIMIT 1",
                (symbol, r["trade_date"]),
            ).fetchone()
            tr = r["hi"] - r["lo"]
            if prev is not None and prev["close"] is not None:
                tr = max(tr, abs(r["hi"] - prev["close"]), abs(r["lo"] - prev["close"]))
            out.append(tr)
        return out
    finally:
        conn.close()


class ReplayDay:
    """One recorded session ready to hand out MEIC's snapshot at any recorded instant."""

    def __init__(self, day: str, cfg: dict, profiles: dict, *, history_db: Path | str | None = None):
        self.day = day
        self.cfg = cfg
        self.profiles = profiles
        self.recorded = quotehistory.RecordedDay(SYMBOL, day, history_db=history_db)
        self.vol = vol_inputs(day, load_cboe("VIX"), load_cboe("VIX1D"))
        self.vix_at = intraday_vix(self.recorded.history_db, [s["ts"] for s in self.recorded.samples])
        lookback = int(cfg.get("regime_atr_lookback_days", 5))
        trs = prior_true_ranges(self.recorded.history_db, SYMBOL, day, lookback)
        self.atr = round(sum(trs) / len(trs), 4) if len(trs) >= lookback else None
        self.widths = paper.union_widths_for_symbol(SYMBOL, cfg, profiles)
        self.extra_deltas = paper.union_short_deltas_for_symbol(SYMBOL, cfg, profiles)

    def snapshot(self, sample: dict, when: datetime) -> dict:
        """`paper_loop.run_iteration`'s snapshot for SPX at `when`, or `{"ok": False, "reason": ...}`."""
        spot = sample.get("underlying_price")
        if not spot:
            return {"ok": False, "reason": "no_spot_price"}
        expiration = sample["expiration"]
        entries = []
        for side, right in (("puts", "P"), ("calls", "C")):
            for strike, q in sample[side].items():
                entries.append(
                    {
                        "strike_price": float(strike),
                        "option_type": right,
                        "streamer_symbol": quotehistory.leg_symbol(SYMBOL, expiration, right, float(strike)),
                        "bid": q.get("bid"),
                        "ask": q.get("ask"),
                        "mid": q.get("mid"),
                        "delta": q.get("delta"),
                    }
                )
        vix = self.vix_at.get(sample["ts"])
        vix_source = "history_intraday"
        if vix is None:
            vix, vix_source = self.vol["vix"], VOL_SOURCE
        iv_rank, iv_pct = iv_rank_of(vix, self.vol["prior_closes"])
        delta_target = paper_loop._delta_target(self.cfg, vix)
        delta_targets = [delta_target] + [d for d in self.extra_deltas if abs(d - delta_target) > 1e-9]
        candidates, leg_quotes, _err = paper_loop.candidates_from_chain(
            entries, spot, self.widths, delta_targets, delta_target
        )
        gex, _stats = self.recorded.gex(sample["ts"], spot, 1)
        if gex.get("ok"):
            gex = {
                "ok": True,
                "gex_positive": gex["gex_positive"],
                "net_gex": gex["net_gex"],
                "gamma_flip": gex.get("gamma_flip"),
                "call_wall": gex.get("call_wall"),
                "put_wall": gex.get("put_wall"),
                "spot": spot,
                "net_gex_vol": None,
            }
        else:
            gex = {"ok": False}
        bounds = self.recorded.bounds(sample["ts"])
        rng = (bounds["day_high"] - bounds["day_low"]) if bounds else None
        return {
            "ok": True,
            "symbol": SYMBOL,
            "date": self.day,
            "now_et": when.strftime("%H:%M"),
            "expiration": expiration,
            "dte": sample.get("dte"),
            "underlying_price": spot,
            "iv_rank": iv_rank,
            "iv_pct": iv_pct,
            "iv_rank_source": self.vol["iv_rank_source"],
            "vix": vix,
            "vix_source": vix_source,
            "vix1d_ratio": self.vol["vix1d_ratio"],
            "atr_5day": self.atr,
            "intraday_range_pct": round(rng / spot, 6) if rng is not None else None,
            "day_open": bounds.get("day_open"),
            "session_quality": paper_loop._session_quality(when),
            "gex": gex,
            "candidates": candidates,
            "leg_quotes": leg_quotes,
            "replayed": True,
        }


def replay_profiles(arms: list[str]) -> tuple[dict, dict]:
    """(base config, {arm: profile}) for `arms`, from `$MEIC_CONFIG` / `$MEIC_RISK_CONFIG` or the
    home's files. Refuses what the replay cannot run honestly."""
    cfg = paper.load_base_config()
    registry = paper.load_profiles()
    out = {}
    for arm in arms:
        if arm.startswith("advised:"):
            raise ValueError(f"arm {arm!r} is advised: its parameters come from a day's advice artifact")
        if arm not in registry:
            raise ValueError(f"unknown arm {arm!r}")
        params = paper._merged_params(cfg, registry[arm])
        syms = [s.upper() for s in params.get("symbols") or cfg.get("symbols") or [SYMBOL]]
        if SYMBOL not in syms:
            raise ValueError(
                f"arm {arm!r} does not trade {SYMBOL} (symbols {syms}); nothing is recorded for it"
            )
        out[arm] = registry[arm]
    return cfg, out


def replay_session(
    day: str,
    ledger_path: Path | str,
    *,
    arms: list[str] | None = None,
    history_db: Path | str | None = None,
    settle_price: float | None = None,
    tick_seconds: float = DEFAULT_TICK_SECONDS,
) -> dict:
    """Replay `day` for `arms` (default control) into a fresh scratch ledger, ticking every
    `tick_seconds`, then settle at `settle_price`."""
    if history_db is None and day < FIRST_SESSION:
        return {"ok": False, "error": f"no quote history before {FIRST_SESSION}"}
    arms = arms or ["control"]
    cfg, profiles = replay_profiles(arms)
    replay = ReplayDay(day, cfg, profiles, history_db=history_db)
    if not replay.recorded.samples:
        return {"ok": False, "error": f"no recorded samples for {SYMBOL} on {day}"}
    if not replay.vol:
        return {"ok": False, "error": f"no VIX open for {day} in {cboe_path('VIX')}"}
    path = Path(ledger_path)
    if path.exists():
        raise FileExistsError(f"{path} exists: a replay writes a fresh scratch ledger")
    refusals: dict[str, int] = {}
    n_ticks = 0
    vix_sources: dict[str, int] = {}
    settled = False
    with meic_db.scratch_writes():
        init = meic_db.call("init_db", str(path))
        if not init.get("ok", True):
            return {"ok": False, "error": f"init_db: {init.get('error')}"}
        taken = ticks(replay.recorded.samples, tick_seconds)
        for sample in taken:
            when = datetime.fromtimestamp(sample["ts"], ET)
            snap = replay.snapshot(sample, when)
            if not snap.get("ok"):
                refusals[snap["reason"]] = refusals.get(snap["reason"], 0) + 1
                continue
            snap.pop("ok")
            vix_sources[snap["vix_source"]] = vix_sources.get(snap["vix_source"], 0) + 1
            with paper.pinned_clock(when):
                paper.process_symbol(snap, str(path), "paper", profiles_filter=arms)
            n_ticks += 1
        if settle_price is not None:
            when = datetime.fromisoformat(f"{day}T{SETTLEMENT_TIME}").replace(tzinfo=ET)
            snap = {
                "symbol": SYMBOL,
                "date": day,
                "now_et": SETTLEMENT_TIME,
                "expiration": day,
                "dte": 0,
                "underlying_price": settle_price,
                "candidates": [],
                "leg_quotes": {},
            }
            with paper.pinned_clock(when):
                paper.process_symbol(snap, str(path), "paper", profiles_filter=arms, evaluate_entries=False)
            settled = True
    return {
        "ok": True,
        "day": day,
        "symbol": SYMBOL,
        "arms": arms,
        "ticks": n_ticks,
        "samples": len(replay.recorded.samples),
        "tick_seconds": tick_seconds,
        "profiles": len(replay.recorded.profiles),
        "refusals": refusals,
        "vix_sources": vix_sources,
        "vol": replay.vol,
        "atr_5day": replay.atr,
        "settled": settled,
        "ledger": str(path),
    }


def _run(args) -> dict:
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    try:
        out = replay_session(
            args.date,
            args.out,
            arms=arms,
            history_db=args.history_db,
            settle_price=args.settle_price,
            tick_seconds=args.tick_seconds,
        )
    except (ValueError, FileExistsError) as exc:
        out = {"ok": False, "error": str(exc)}
    return {**out, "settle_price": args.settle_price}


def _check(args) -> dict:
    from cherrypick.meic import replay_check  # function-local: replay_check imports this module

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    sessions = []
    for day in [d.strip() for d in args.dates.split(",") if d.strip()]:
        try:
            sessions.append(
                replay_check.decision_check(day, arms, history_db=args.history_db, paper_db=args.paper_db)
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    summary = replay_check.summarise(sessions)
    if not args.detail:
        for s in sessions:
            s.pop("matched_entries", None)
            s.pop("max_cost_diffs", None)
    return {"ok": True, "sessions": sessions, "summary": summary}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m cherrypick.meic.replay", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="replay one recorded session into a scratch ledger")
    run.add_argument("--date", required=True)
    run.add_argument("--out", required=True, help="a ledger path that must not exist")
    run.add_argument("--arms", default="control", help="comma-separated registry arms")
    run.add_argument(
        "--history-db", default=None, help="a history in the recorder's schema (default: the recorder's)"
    )
    run.add_argument("--settle-price", type=float, default=None)
    run.add_argument(
        "--tick-seconds",
        type=float,
        default=DEFAULT_TICK_SECONDS,
        help="the loop cadence replayed (default 60, paper's)",
    )
    run.set_defaults(func=_run)
    chk = sub.add_parser("check", help="validate the replay against MEIC paper on recorded sessions")
    chk.add_argument("--dates", required=True, help="comma-separated recorded sessions")
    chk.add_argument("--arms", default="control", help="comma-separated SPX registry arms")
    chk.add_argument("--history-db", default=None, help="default: the recorder's")
    chk.add_argument("--paper-db", default=None, help="default: MEIC's paper ledger")
    chk.add_argument("--detail", action="store_true", help="every matched entry and path difference")
    chk.set_defaults(func=_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = args.func(args)
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
