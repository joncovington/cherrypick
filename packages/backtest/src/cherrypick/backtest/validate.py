"""Validate vendor sessions against the gex recorder's on the days both exist, before any history run.

Three layers, each answering whether a backtest on vendor data would see what the suite saw live:

1. **Quotes.** Every recorded quote against the vendor's quote standing at the same instant (the
   recorded quote's own time, `ts - age`), on the same strike and side: the share matched, the share
   identical, and the size of the mid gap. Spot the same way.
2. **Profile.** The GEX surface the replay rebuilds (`core.quotehistory.profile_surface` over each
   profile row, the build every replay uses) from the recorder's profile and from the vendor's computed
   one: walls, gamma flip, and the sign of net GEX.
3. **Decisions.** The validated module's own check (`CHECKS`, one entry per module: flies'
   `replay-check`, MEIC's `meic.replay check`) run on the recorder's history and on the vendor's, each
   against paper's own book. Run as a subprocess (this package imports no strategy module), and each
   module's report unlocks only that module's history.

**The declared bar for unlocking history runs (2026-10-17):** at least `UNLOCK_SESSIONS` sessions in
both stores, and the vendor-sourced replay at least as good as the recorder-sourced one on every one of
the module's declared checks. The question is whether the vendor's data is as good as our own recording,
not whether the replay is perfect: the recorder-sourced replay itself fails flies' completion-timing
check, and a bar it cannot meet would measure the replay, not the data.

**Amended the same day, after the first four sessions were read: the recording is compared at the
vendor's cadence.** The 60 s store matched paper's entries far less often than the 15 s recording, and
every miss was the replay's one sample in flies' 30 s window landing on the other side of a strike
boundary (a neighbouring centre, or the other side at the same centre), none a quote the vendor got
wrong; the recording thinned to the vendor's minute marks matched less often still. So the bar as first
written compared a cadence with a cadence, and no 60 s store could ever pass it. Now:

- **Data (the bar):** the vendor-sourced replay against the recording thinned to the vendor's own
  sample instants (`thin_to_vendor_instants`), on every declared check, at the same session count.
  When the two stores share a cadence, nothing is thinned and this is the bar as first written.
- **Cadence (reported, not a bar):** the thinned recording against the full one, each check's measure
  side by side, and the completion outcome (did the position complete at all that session), which is
  what moves P&L where the timing check does not. What a minute costs is measured, never assumed.

The thresholds, the checks and `UNLOCK_SESSIONS` are unchanged.

**Amended 2026-10-25, before MEIC's first validation run: the bar compares measures, not pass/fail.**
Comparing each check's pass/fail let a vendor through on any check the recording also failed, however
far behind it was (flies' entries and completions both read "at least as good" that way). Now every
declared check's MEASURE must be within a declared tolerance of the recording's at the vendor's
cadence (`SHARE_TOLERANCE`, `GAP_TOLERANCE_*`), whether the check passes or not; the pass/fail
comparison is kept in the report for the record. The quote layer gains the same comparison at the
vendor's own sample instants (`at_vendor_instants`): its share at every recorded quote mostly measured
cadence. The quote layer is reported, not a bar.

Results are derived from licensed data: they are written under the home's results directory and
nowhere else (see CLAUDE.md).
"""

from __future__ import annotations

import bisect
import json
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from cherrypick.core import db as core_db
from cherrypick.core import progress, quotehistory

from . import paths, store

#: A vendor quote older than this, relative to the recorded instant, is not a match.
QUOTE_MATCH_SECONDS = 30.0
#: The replay's profile staleness bound (`flies.replay.PROFILE_MAX_AGE_SECONDS`).
PROFILE_MATCH_SECONDS = 360.0
#: Sessions needed before history runs unlock: flies' own stage-2 minimum.
UNLOCK_SESSIONS = 10
#: The measure bar (declared 2026-10-25, before MEIC's first validation run): on every declared check
#: the vendor-sourced replay's MEASURE may be worse than the recording's at the vendor's cadence by at
#: most this much, whether or not the check passes. A share (entries, completions, episodes) by an
#: absolute margin; a dollar gap by the larger of an absolute margin and a share of the recording's.
#: Before it the bar compared pass/fail only, so a check both sides failed read "at least as good"
#: however far behind the vendor was.
SHARE_TOLERANCE = 0.05
GAP_TOLERANCE_ABS = 0.02
GAP_TOLERANCE_REL = 0.25
#: A store is coarser than the recording when its median sample gap is this many times the recording's.
COARSER_RATIO = 1.5


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 4)


def _summary(gaps: list[float]) -> dict:
    a = [abs(g) for g in gaps]
    return {
        "median": round(statistics.median(a), 4) if a else None,
        "p90": _pct(a, 0.90),
        "p99": _pct(a, 0.99),
        "mean_signed": round(statistics.fmean(gaps), 4) if gaps else None,
    }


def quotes_layer(recorder_db: Path, vendor_db: Path, symbol: str, day: str) -> dict:
    rec = quotehistory.session(symbol, day, history_db=recorder_db)
    ven = quotehistory.session(symbol, day, history_db=vendor_db)
    if not rec or not ven:
        return {"ok": False, "reason": "no recorder samples" if not rec else "no vendor samples"}
    ven_ts = [s["ts"] for s in ven]
    total = matched = identical = 0
    mid_gaps, spot_gaps = [], []
    for s in rec:
        i = bisect.bisect_right(ven_ts, s["ts"]) - 1
        if (
            i >= 0
            and s["underlying_price"]
            and ven[i]["underlying_price"]
            and s["ts"] - ven_ts[i] <= QUOTE_MATCH_SECONDS
        ):
            spot_gaps.append(s["underlying_price"] - ven[i]["underlying_price"])
        for side in ("puts", "calls"):
            for strike, q in s[side].items():
                total += 1
                instant = s["ts"] - (q.get("age_seconds") or 0.0)
                j = bisect.bisect_right(ven_ts, instant) - 1
                if j < 0 or instant - ven_ts[j] > QUOTE_MATCH_SECONDS:
                    continue
                v = ven[j][side].get(strike)
                if v is None:
                    continue
                matched += 1
                if abs(v["bid"] - q["bid"]) < 1e-9 and abs(v["ask"] - q["ask"]) < 1e-9:
                    identical += 1
                mid_gaps.append(q["mid"] - v["mid"])
    return {
        "ok": True,
        "recorded_quotes": total,
        "matched": round(matched / total, 4) if total else None,
        "identical": round(identical / matched, 4) if matched else None,
        "mid_gap": _summary(mid_gaps),
        "spot_gap": _summary(spot_gaps),
        "at_vendor_instants": _at_vendor_instants(rec, ven),
    }


def _at_vendor_instants(rec: list[dict], ven: list[dict]) -> dict:
    """The quote comparison at the vendor's own sample instants (2026-10-25): for each vendor sample,
    the recorded sample nearest it within QUOTE_MATCH_SECONDS, and every strike and side both quote.
    The share above is taken at every RECORDED quote, so against a coarser vendor it mostly measures
    cadence (a 15 s recording against 60 s samples matches about half); this measures the data."""
    rec_ts = [s["ts"] for s in rec]
    paired = shared = identical = 0
    mid_gaps = []
    for v in ven:
        i = bisect.bisect_left(rec_ts, v["ts"])
        best = min(
            (j for j in (i - 1, i) if 0 <= j < len(rec_ts)),
            key=lambda j: abs(rec_ts[j] - v["ts"]),
            default=None,
        )
        if best is None or abs(rec_ts[best] - v["ts"]) > QUOTE_MATCH_SECONDS:
            continue
        paired += 1
        r = rec[best]
        for side in ("puts", "calls"):
            for strike, vq in v[side].items():
                rq = r[side].get(strike)
                if rq is None:
                    continue
                shared += 1
                if abs(vq["bid"] - rq["bid"]) < 1e-9 and abs(vq["ask"] - rq["ask"]) < 1e-9:
                    identical += 1
                mid_gaps.append(rq["mid"] - vq["mid"])
    return {
        "vendor_samples": len(ven),
        "paired_samples": round(paired / len(ven), 4) if ven else None,
        "shared_quotes": shared,
        "identical": round(identical / shared, 4) if shared else None,
        "mid_gap": _summary(mid_gaps),
    }


def _profiles(db: Path, symbol: str, day: str) -> list[tuple[float, list[dict]]]:
    if not Path(db).exists():
        return []
    conn = core_db.connect_ro(db)
    try:
        grouped: dict[float, list[dict]] = {}
        for r in conn.execute(
            "SELECT ts, strike, call_oi, put_oi, call_gamma, put_gamma FROM gex_profile_history "
            "WHERE symbol = ? AND trade_date = ? ORDER BY ts",
            (symbol.upper(), day),
        ):
            grouped.setdefault(float(r["ts"]), []).append(dict(r))
        return sorted(grouped.items())
    finally:
        conn.close()


def _spots(db: Path, symbol: str, day: str) -> list[tuple[float, float]]:
    conn = core_db.connect_ro(db)
    try:
        return [
            (float(r["ts"]), float(r["spot"]))
            for r in conn.execute(
                "SELECT ts, spot FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                (symbol.upper(), day),
            )
        ]
    finally:
        conn.close()


def surface(rows: list[dict], spot: float) -> dict:
    """The replay's GEX surface from one profile row set: `core.quotehistory.profile_surface`, the build
    every replay uses."""
    return quotehistory.profile_surface(rows, spot)


def profile_layer(recorder_db: Path, vendor_db: Path, symbol: str, day: str) -> dict:
    rec, ven = _profiles(recorder_db, symbol, day), _profiles(vendor_db, symbol, day)
    if not rec or not ven:
        return {"ok": False, "reason": "no recorder profile" if not rec else "no vendor profile"}
    trail = _spots(recorder_db, symbol, day)
    trail_ts = [t for t, _ in trail]
    ven_ts = [t for t, _ in ven]
    pairs = call_wall = put_wall = sign = 0
    flip_gaps = []
    strikes = {
        "recorder_median": statistics.median(len(r) for _, r in rec),
        "vendor_median": statistics.median(len(r) for _, r in ven),
    }
    for ts, rows in rec:
        k = bisect.bisect_right(trail_ts, ts) - 1
        j = bisect.bisect_right(ven_ts, ts) - 1
        if k < 0 or j < 0 or ts - ven_ts[j] > PROFILE_MATCH_SECONDS:
            continue
        spot = trail[k][1]
        a, b = surface(rows, spot), surface(ven[j][1], spot)
        if not (a.get("ok", True) and b.get("ok", True)) or "net_gex" not in a or "net_gex" not in b:
            continue
        pairs += 1
        call_wall += a.get("call_wall") == b.get("call_wall")
        put_wall += a.get("put_wall") == b.get("put_wall")
        sign += (a["net_gex"] >= 0) == (b["net_gex"] >= 0)
        if a.get("gamma_flip") is not None and b.get("gamma_flip") is not None:
            flip_gaps.append(a["gamma_flip"] - b["gamma_flip"])
    return {
        "ok": pairs > 0,
        "pairs": pairs,
        "call_wall_same": round(call_wall / pairs, 4) if pairs else None,
        "put_wall_same": round(put_wall / pairs, 4) if pairs else None,
        "net_gex_sign_same": round(sign / pairs, 4) if pairs else None,
        "gamma_flip_gap": _summary(flip_gaps),
        # A profile with fewer strikes is a narrower surface; walls and the flip move with coverage.
        "strikes_per_profile": strikes,
    }


def _flies_cli(args: list[str]) -> dict:
    """`python -m cherrypick.flies.cli <args>` with this interpreter; its JSON reply, or the failure."""
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.flies.cli", *args],
        stdout=subprocess.PIPE,
        text=True,
        timeout=3600,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        # The child's stderr went straight to ours (the queue log), as it happened.
        return {
            "ok": False,
            "error": (proc.stdout or "")[-800:] or "no JSON reply; see the log",
            "returncode": proc.returncode,
        }


def _meic_cli(args: list[str]) -> dict:
    """`python -m cherrypick.meic.replay <args>` with this interpreter; its JSON reply, or the failure."""
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.replay", *args],
        stdout=subprocess.PIPE,
        text=True,
        timeout=36000,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        # The child's stderr went straight to ours (the queue log), as it happened.
        return {
            "ok": False,
            "error": (proc.stdout or "")[-800:] or "no JSON reply; see the log",
            "returncode": proc.returncode,
        }


#: Per module: the check's CLI, the verb that runs it, and what its summary reports beside the pass
#: test (flies' completion outcome; MEIC's share of paper's condors the full-day replay paired), which
#: `cadence_cost` carries for each source. Each module validates its own replay.
CHECKS = {
    "flies": (_flies_cli, ("replay-check",), ("completion_outcome_agreement",)),
    "meic": (_meic_cli, ("check",), ("path_pairs",)),
}

#: MEIC's delta sources (`meic.paper_loop.DELTA_SOURCES`), spelled here because this package imports
#: no strategy module; `tests/test_validate.py` reads MEIC's source and holds the two equal.
DELTA_SOURCES = ("stream", "computed")


def _sample_instants(db: Path, symbol: str, day: str) -> list[float]:
    conn = core_db.connect_ro(db)
    try:
        return [
            float(r["ts"])
            for r in conn.execute(
                "SELECT ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                (symbol.upper(), day),
            )
        ]
    finally:
        conn.close()


def median_gap(db: Path, symbol: str, dates: list[str]) -> float | None:
    """The store's median seconds between quote samples over `dates`."""
    gaps = []
    for day in dates:
        ts = _sample_instants(db, symbol, day)
        gaps.extend(b - a for a, b in zip(ts, ts[1:], strict=False))
    return statistics.median(gaps) if gaps else None


def _nearest(rows: list[tuple], instants: list[float]) -> set:
    """The first element of each row nearest each instant, within QUOTE_MATCH_SECONDS. `rows` are
    (key, ts), sorted by ts."""
    ts = [r[1] for r in rows]
    keep = set()
    for t in instants:
        i = bisect.bisect_left(ts, t)
        best = min((j for j in (i - 1, i) if 0 <= j < len(ts)), key=lambda j: abs(ts[j] - t), default=None)
        if best is not None and abs(ts[best] - t) <= QUOTE_MATCH_SECONDS:
            keep.add(rows[best][0])
    return keep


def thin_to_vendor_instants(
    recorder_db: Path, vendor_db: Path, symbol: str, dates: list[str], out: Path
) -> Path:
    """A copy of the recording with each of `dates` cut to the vendor's own sample instants: for every
    vendor sample, the recorded quote sample and spot row nearest it (within QUOTE_MATCH_SECONDS), the
    rest dropped. The recording's prices at the vendor's cadence, so a replay over it differs from the
    vendor's only by the data. Profiles and closes are kept whole: neither is sampled per tick."""
    src = sqlite3.connect(f"file:{recorder_db}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        sym = symbol.upper()
        for day in dates:
            instants = _sample_instants(vendor_db, symbol, day)
            samples = dst.execute(
                "SELECT id, ts FROM gex_quote_samples WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                (sym, day),
            ).fetchall()
            drop = [(i,) for i, _ in samples if i not in _nearest(samples, instants)]
            dst.executemany("DELETE FROM gex_quotes WHERE sample_id = ?", drop)
            dst.executemany("DELETE FROM gex_quote_samples WHERE id = ?", drop)
            spots = dst.execute(
                "SELECT rowid, ts FROM gex_spot_history WHERE symbol = ? AND trade_date = ? ORDER BY ts",
                (sym, day),
            ).fetchall()
            keep = _nearest(spots, instants)
            dst.executemany(
                "DELETE FROM gex_spot_history WHERE rowid = ?", [(r,) for r, _ in spots if r not in keep]
            )
        dst.commit()
    finally:
        src.close()
        dst.close()
    return out


def _check(runner, db: Path, dates: list[str], command: tuple = ("replay-check",)) -> dict:
    reply = runner([*command, "--dates", ",".join(dates), "--history-db", str(db)])
    return reply.get("summary", reply)


def _direction(thresholds: dict, check: str) -> str | None:
    """'min' when higher is better for this check's measure, 'max' when lower is; from the module's
    own threshold names (`<check>_min` / `<check>_max`)."""
    if f"{check}_min" in thresholds:
        return "min"
    if f"{check}_max" in thresholds:
        return "max"
    return None


def measure_within(check: str, direction: str | None, ref: float | None, ven: float | None) -> bool:
    """The vendor's measure no worse than the reference's by more than the declared tolerance. A
    measure the vendor lacks fails; one the reference lacks cannot be compared and fails too."""
    if ref is None or ven is None or direction is None:
        return False
    is_share = not (check.startswith("credit") or check.startswith("max_cost"))
    tol = SHARE_TOLERANCE if is_share else max(GAP_TOLERANCE_ABS, GAP_TOLERANCE_REL * abs(ref))
    return ven >= ref - tol if direction == "min" else ven <= ref + tol


def _sessions(summary: dict) -> int:
    return (summary.get("measures") or {}).get("sessions") or 0


def decisions_layer(
    recorder_db: Path,
    vendor_db: Path,
    dates: list[str],
    runner=_flies_cli,
    *,
    thinned_db: Path | None = None,
    command: tuple = ("replay-check",),
    outcome_keys: tuple = ("completion_outcome_agreement",),
    rep=None,
) -> dict:
    """A module's replay check (flies' `replay-check` by default) on the recording, on the vendor's
    store and, when the vendor's store is coarser, on the recording thinned to the vendor's instants
    (`thinned_db`). The bar compares the vendor with the recording at the vendor's cadence (module
    docstring, amended 2026-10-17)."""
    sources = [("recorder", recorder_db), ("vendor", vendor_db)]
    if thinned_db is not None:
        sources.append(("recorder_at_vendor_cadence", thinned_db))
    if rep is not None:
        rep.phase("decisions", total=len(sources), unit="replays")
    out = {}
    for name, db in sources:
        if rep is not None:
            rep.at("decisions", name)
        out[name] = _check(runner, db, dates, command)
        if rep is not None:
            rep.advance("decisions")
    basis = "recorder"
    if thinned_db is not None:
        basis = "recorder_at_vendor_cadence"
        full, thin = out["recorder"], out["recorder_at_vendor_cadence"]
        out["cadence_cost"] = {
            "measures": {
                k: {"full": v, "at_vendor_cadence": (thin.get("measures") or {}).get(k)}
                for k, v in (full.get("measures") or {}).items()
                if k != "sessions"
            },
            **{
                key: {
                    "full": full.get(key),
                    "at_vendor_cadence": thin.get(key),
                    "vendor": out["vendor"].get(key),
                }
                for key in outcome_keys
            },
        }
    ref, ven = out[basis], out["vendor"]
    checks_r, checks_v = ref.get("checks") or {}, ven.get("checks") or {}
    out["data_basis"] = basis
    # The pass/fail comparison, kept for the record: it is no longer the bar (see SHARE_TOLERANCE).
    out["vendor_at_least_recorder"] = {k: bool(checks_v.get(k)) or not checks_r.get(k) for k in checks_r}
    thresholds = ref.get("thresholds") or ven.get("thresholds") or {}
    m_ref, m_ven = ref.get("measures") or {}, ven.get("measures") or {}
    out["vendor_within_tolerance"] = {
        k: measure_within(k, _direction(thresholds, k), m_ref.get(k), m_ven.get(k)) for k in checks_r
    }
    out["tolerance"] = {"share": SHARE_TOLERANCE, "gap_abs": GAP_TOLERANCE_ABS, "gap_rel": GAP_TOLERANCE_REL}
    sessions = min(_sessions(out["recorder"]), _sessions(ref), _sessions(ven))
    out["sessions_compared"] = sessions
    out["unlocks_history"] = (
        bool(checks_r) and sessions >= UNLOCK_SESSIONS and all(out["vendor_within_tolerance"].values())
    )
    return out


def run(
    settings: dict,
    symbol: str,
    dates: list[str],
    *,
    recorder_db: Path | None = None,
    cadence: float | None = None,
    runner=None,
    module: str = "flies",
    arms: list[str] | None = None,
    delta_source: str | None = None,
) -> dict:
    """Validate `module`'s replay on the vendor store against the recording (`arms`: MEIC's arms to
    check, default its check's own). The report names the module, so each unlocks only its own.

    `delta_source` (MEIC only, 2026-10-27) replays both sources on one delta. By default each session
    replays on the delta paper ran with, and before 2026-10-27 that was the data feed's, which lags
    its quotes by about a minute: the recording carries that lag and the vendor's delta does not, so
    the vendor replay could not choose paper's strikes whatever its data. "computed" picks strikes
    from each source's own mids (`core.chaindelta`), so the decisions layer compares the data."""
    if module not in CHECKS:
        raise ValueError(f"module must be one of {sorted(CHECKS)}")
    if delta_source is not None and (module != "meic" or delta_source not in DELTA_SOURCES):
        raise ValueError(f"delta_source is MEIC's, one of {DELTA_SOURCES}")
    default_runner, command, outcome_keys = CHECKS[module]
    runner = runner or default_runner
    if module == "meic" and arms:
        command = (*command, "--arms", ",".join(arms))
    if delta_source is not None:
        command = (*command, "--delta-source", delta_source)
    recorder_db = Path(recorder_db) if recorder_db else quotehistory.default_history_db()
    vendor_db = store.db_path(settings, symbol, cadence)
    report = {
        "module": module,
        "symbol": symbol.upper(),
        "dates": dates,
        "recorder_db": str(recorder_db),
        "vendor_db": str(vendor_db),
        # None: each session on the delta paper ran with (`delta_source_since`).
        "delta_source": delta_source,
        "sessions": {},
    }
    rep = progress.Reporter(f"backtest-validate-{module}")
    rep.phase("layers", total=len(dates), unit="sessions")
    for day in dates:
        rep.at("layers", day)
        report["sessions"][day] = {
            "quotes": quotes_layer(recorder_db, vendor_db, symbol, day),
            "profile": profile_layer(recorder_db, vendor_db, symbol, day),
        }
        rep.advance("layers")
    rep.close_phase("layers")
    rec_gap, ven_gap = median_gap(recorder_db, symbol, dates), median_gap(vendor_db, symbol, dates)
    report["sample_gap_seconds"] = {"recorder": rec_gap, "vendor": ven_gap}
    if rec_gap and ven_gap and ven_gap >= COARSER_RATIO * rec_gap:
        # The thinned copy is our own recording cut to the vendor's instants; it lives only for the check.
        with tempfile.TemporaryDirectory() as tmp:
            thinned = thin_to_vendor_instants(recorder_db, vendor_db, symbol, dates, Path(tmp) / "thinned.db")
            report["decisions"] = decisions_layer(
                recorder_db,
                vendor_db,
                dates,
                runner=runner,
                thinned_db=thinned,
                command=command,
                outcome_keys=outcome_keys,
                rep=rep,
            )
    else:
        report["decisions"] = decisions_layer(
            recorder_db, vendor_db, dates, runner=runner, command=command, outcome_keys=outcome_keys, rep=rep
        )
    out_dir = paths.results_dir() / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    # flies' reports keep the name they have always had; another module's carry its name first.
    prefix = "" if module == "flies" else f"{module}-"
    # A chosen delta source is its own report, never written over the as-run one for the same dates.
    suffix = "" if delta_source is None else f"-delta-{delta_source}"
    path = out_dir / f"{prefix}{symbol.upper()}-{dates[0]}_{dates[-1]}{suffix}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    report["written"] = str(path)
    rep.finish("done")
    return report


def recorded_days(recorder_db: Path, vendor_db: Path, symbol: str) -> list[str]:
    """Sessions present in both stores: the days validation can run on."""

    def days(db):
        if not Path(db).exists():
            return set()
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT trade_date FROM gex_quote_samples WHERE symbol = ?", (symbol.upper(),)
                )
            }
        finally:
            conn.close()

    return sorted(days(recorder_db) & days(vendor_db))
