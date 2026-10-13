"""cherrypick.core.compare — every arm of every module, beside its control, on one footing.

The suite runs a dozen A/Bs at once across eight ledgers, and until this existed each surface that
compared two arms did it its own way (pooled trade means in one place, an unpaired session bootstrap
in another, a hand-cut window in a third). This module reads every module's closed rows through
`core.ledgers` -- the one home for what net, cost and session mean per schema -- and hands each arm
to `core.stats`, so the comparison is computed once and the console renders it
(`python -m cherrypick.core.compare --json`, the bridge).

**The window is the module's own evidence window** (`scope="era"`, the default):

  * flies and MEIC journal `measurement_breaks` with a scope and a kind. The era starts at the latest
    book-wide break on or before the session (`regimecuts.era_bounds`, the same rule the regime-cuts
    artifact uses), and an arm added later starts at its own `arm_added` break.
  * bwb, curve, calendars, earnings and contango journal key/value breaks (`ledgerstore`), which
    carry no scope: every one bounds the era, as earnings' own CLI says ("never pool across that
    date"), except the roster and damage notes in `NON_BOUNDING_KEYS`, which are listed as caveats.
  * pmcc stamps its era on every row; its scope is the rows stamped with the latest journaled `era`
    break, never a date guess that could disagree with the stamp (`ledgers.ERA_SCHEMAS`).

`scope="all"` reads every closed row and says so. A break dated after the session binds nothing.

**What a session is, for pairing.** For the 0DTE modules (flies, MEIC) it is the trading day. For
a multi-day structure it is the session the position was ENTERED (`entry_session` on the ledger
record): the arms enter together and exit by different rules, so pairing on the close would set
calendars' Friday-bell control against its path book's Monday close and find almost no shared
days. Each arm's own session nets, interval and drawdown stay on the close, the day the money was
realised.

**An arm is compared with its control on their common sessions**: inside the overlap of the two
windows (the later first session to the earlier last), the sessions on which BOTH closed trades
(`core.stats.paired_diffs(fill=None)`). Sessions only one side closed are dropped and counted
(`arm_only_sessions`, `control_only_sessions`), because no ledger records whether an arm was RUNNING
on a day it closed nothing: an advised book whose artifact was not applied that day would otherwise
read as a zero against control's whole session (on MEIC, tens of thousands of dollars of
difference that the parameter never caused). The cost is that a gate which blocked an entire day
loses that day from its pair; the counts say how many. The control is `control` in the same
module; for `advised:<name>` books it is the
experiment's own `base_profile` from the advisor's store (earnings appends the strategy); for a
`<prefix>:<arm>` arm it is `<prefix>:control` when one exists. Earnings' `strat_test:<strategy>`
books are each a strategy's own baseline and have no control.

**Size-normalised figures.** A paired difference in dollars a session compares books of whatever
size they are: MEIC's capped arms read -$22k to -$31k a session behind a control that takes ~270
entries a day against their ~6, which is mostly size, not the parameter. So every arm also carries
`net_per_entry` and `net_per_100_risk` (net per $100 of the capital `core.ledgers` records per trade:
max loss, or buying power, as each module's ledger states it; only trades with a known capital
count, and how many is said), and every pair carries the same paired comparison on the per-session
normalised figures (`per_entry`, `per_100_risk`), keyed and windowed exactly as the dollar pair. A
module whose ledger records no capital (flies) has no risk-normalised figure, never a zero.

Read-only, deterministic (seeded bootstraps), no network. Nothing here labels an arm better or
worse: the interval, the sign test and the projection are numbers, and the thresholds belong to
whoever reads them.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import ledgers as _ledgers
from cherrypick.core import metrics as _metrics
from cherrypick.core import regimecuts as _regimecuts
from cherrypick.core import stats as _stats

SCHEMA_VERSION = 1

#: Module -> ledger schema, for every module with a paper ledger `core.ledgers` can read.
#: `test_every_ledger_schema_is_compared` fails when a reader exists that this map does not reach.
MODULE_SCHEMAS: dict[str, str] = {
    "flies": "fly_book",
    "meic": "meic_ic",
    "bwb": "bwb_132",
    "curve": "curve_vx",
    "calendars": "dc_week",
    "pmcc": "pmcc",
    "earnings": "earnings",
    "contango": "contango_etf",
}

#: Key/value journal keys that never bound an era: a damaged session, a GEX tag-surface fix, and
#: roster notes (an arm or book joining, advice switched off), which change what is run beside the
#: other arms and not how those arms trade.
NON_BOUNDING_KEYS = frozenset(_regimecuts.NON_BOUNDING_KINDS | {"arms", "wall_book_added", "advice"})

#: Schemas whose positions open and settle in one session: paired on the trading day. Every other
#: schema is paired on the session its positions were entered.
SAME_DAY_SCHEMAS = frozenset({"fly_book", "meic_ic"})

LEVEL = _stats.DEFAULT_LEVEL
RESAMPLES = _stats.DEFAULT_RESAMPLES
SEED = _stats.DEFAULT_SEED

COSTS_NOTE = (
    "costs = every modelled cost the module's ledger subtracts from gross: trading fees, settlement, "
    "and slippage where the module charges it as a cost (bwb, calendars, pmcc, curve, earnings, "
    "contango). flies and MEIC concede slippage inside the fill, so it is inside gross there."
)


# --------------------------------------------------------------------------- reading
def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def read_breaks(conn: sqlite3.Connection) -> list[dict]:
    """The module's `measurement_breaks` in one shape, `{break_date, scope, kind, reason, new_value}`,
    from either journal schema. A ledger with no journal has no breaks."""
    cols = _cols(conn, "measurement_breaks")
    if not cols:
        return []
    if "scope" in cols:
        rows = conn.execute("SELECT break_date, scope, kind, reason FROM measurement_breaks").fetchall()
        return [
            {"break_date": r[0], "scope": r[1] or "*", "kind": r[2], "reason": r[3], "new_value": None}
            for r in rows
        ]
    rows = conn.execute("SELECT break_date, key, note, new_value FROM measurement_breaks").fetchall()
    return [{"break_date": r[0], "scope": "*", "kind": r[1], "reason": r[2], "new_value": r[3]} for r in rows]


def experiments_index(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """`{(module, tag): {id, base_profile, name, status}}` from the advisor's store, read-only. An
    absent or unreadable store is an empty index: advised books then pair by their legacy tag."""
    path = path or (_home.data_dir("advisor") / "advisor.db")
    if not Path(path).exists():
        return {}
    try:
        conn = _ledgers.connect_ro(path)
    except sqlite3.Error:
        return {}
    try:
        cols = _cols(conn, "experiments")
        if not {"module", "base_profile", "name"} <= cols:
            return {}
        tag_col = "tag" if "tag" in cols else "NULL"
        out: dict[tuple[str, str], dict] = {}
        for r in conn.execute(
            f"SELECT id, module, base_profile, name, status, {tag_col} AS tag FROM experiments"
        ):
            tag = r["tag"] or (f"advised:{r['name']}" if r["name"] else None)
            if tag:
                out[(r["module"], tag)] = {
                    "id": r["id"],
                    "base_profile": r["base_profile"],
                    "name": r["name"],
                    "status": r["status"],
                }
        return out
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


# --------------------------------------------------------------------------- windows
def era_window(module: str, breaks: list[dict], session: str) -> dict:
    """The module's evidence window as of `session`: `{start, bounding_break, arm_starts, caveats,
    ignored_future}`. Roster/damage keys of a key/value journal are moved to the caveats first."""
    bounding, extra_caveats = [], []
    for b in breaks:
        if (
            b["scope"] == "*"
            and b["kind"] in NON_BOUNDING_KEYS
            and b["kind"] not in _regimecuts.NON_BOUNDING_KINDS
        ):
            if str(b["break_date"]) <= session:
                extra_caveats.append({k: b[k] for k in ("break_date", "kind", "reason")})
            continue
        bounding.append(b)
    era = _regimecuts.era_bounds(bounding, session)
    caveats = sorted(era["caveats"] + extra_caveats, key=lambda c: (str(c["break_date"]), str(c["kind"])))
    bb = era["bounding_break"]
    return {
        "start": era["start"],
        "bounding_break": (
            {"break_date": bb["break_date"], "kind": bb["kind"], "reason": bb["reason"]} if bb else None
        ),
        "arm_starts": {arm: date for arm, (date, _row) in era["arm_starts"].items()},
        "caveats": caveats,
        "ignored_future": era["ignored_future"],
    }


def current_era_stamp(breaks: list[dict], session: str) -> str | None:
    """The newest journaled `era` value on or before `session` -- pmcc's stamp for its current era."""
    eras = sorted(
        (b for b in breaks if b["kind"] == "era" and b.get("new_value") and str(b["break_date"]) <= session),
        key=lambda b: str(b["break_date"]),
    )
    return eras[-1]["new_value"] if eras else None


# --------------------------------------------------------------------------- controls
def control_for(
    module: str, arm: str, arms: Iterable[str], experiments: Mapping[tuple[str, str], dict]
) -> str | None:
    """The arm this one is compared against, or None (it is itself a control, or its control has
    no rows in scope)."""
    present = set(arms)
    candidate: str | None = None
    if arm.startswith("advised:"):
        tag, strategy = arm, None
        if module == "earnings" and arm.count(":") >= 2:
            tag, strategy = arm.rsplit(":", 1)
        exp = experiments.get((module, tag))
        base = exp["base_profile"] if exp else tag[len("advised:") :]
        candidate = f"{base}:{strategy}" if strategy else base
    elif ":" in arm and module != "earnings":
        prefix = arm.rsplit(":", 1)[0]
        candidate = f"{prefix}:control"
    elif arm != "control":
        candidate = "control"
    if candidate == arm or candidate not in present:
        return None
    return candidate


def _arm_order(name: str) -> tuple:
    return (name != "control", name.startswith("advised:"), name)


# --------------------------------------------------------------------------- the reading
def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _capital(record: dict) -> float | None:
    """The trade's recorded capital at risk, or None when the ledger did not record one (or
    recorded a non-positive one, which cannot be divided by)."""
    cap = record.get("capital")
    try:
        cap = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        return None
    return cap if cap is not None and cap > 0 else None


def normalised_session_values(
    records: list[dict], per: str, *, session_key: str = "session"
) -> dict[str, float]:
    """`{session: normalised net}` for one arm, in session order. `per="entry"`: the session's net
    over its entries. `per="risk"`: 100 x the session's net over its recorded capital, from the trades
    whose capital is known; a session with none is dropped, never read as zero."""
    nets: dict[str, float] = {}
    denom: dict[str, float] = {}
    for r in records:
        s = r.get(session_key)
        if not s or r.get("net_pnl") is None:  # undated, or n/r (unscored)
            continue
        if per == "entry":
            weight = 1.0
        elif per == "risk":
            weight = _capital(r)
            if weight is None:
                continue
        else:
            raise ValueError(f"normalised_session_values: per must be 'entry' or 'risk', not {per!r}")
        nets[str(s)] = nets.get(str(s), 0.0) + float(r["net_pnl"])
        denom[str(s)] = denom.get(str(s), 0.0) + weight
    scale = 100.0 if per == "risk" else 1.0
    return {s: scale * nets[s] / denom[s] for s in sorted(nets) if denom[s]}


def arm_reading(
    records: list[dict], *, level: float = LEVEL, resamples: int = RESAMPLES, seed: int = SEED
) -> dict:
    """One arm's own numbers over its in-scope closed rows. Money in whole-position dollars, net
    after every cost; `max_drawdown` is the peak-to-trough fall of the per-SESSION equity path.
    `net_per_entry` and `net_per_100_risk` are the size-normalised figures (module docstring).
    A closed row the ledger left without its money (`net_pnl` None, n/r -- `core.ledgers`) is no
    trade's result: it joins no figure here and is counted as `unscored`, as `core.metrics` does."""
    scored = [r for r in records if r.get("net_pnl") is not None]
    unscored = len(records) - len(scored)
    ordered = sorted(scored, key=lambda r: r.get("session") or "")
    nets = [float(r["net_pnl"]) for r in ordered]
    sv = _stats.session_values(ordered)
    svals = list(sv.values())
    mean, sd = _stats.mean_sd(svals)
    ci = _stats.bootstrap_ci(svals, level=level, resamples=resamples, seed=seed) if len(svals) >= 2 else None
    excludes = (ci[0] > 0 or ci[1] < 0) if ci else None
    n = len(nets)
    known = [(float(r["net_pnl"]), cap) for r in ordered if (cap := _capital(r)) is not None]
    risk = sum(cap for _, cap in known)
    return {
        "trades": n,
        "unscored": unscored,
        "sessions": len(sv),
        "first": next(iter(sv), None),
        "last": next(reversed(sv), None) if sv else None,
        "gross": _r(sum(float(r["gross_pnl"] or 0.0) for r in ordered)),
        "costs": _r(sum(float(r["cost"] or 0.0) for r in ordered)),
        "net": _r(sum(nets)),
        "win_rate": _r(sum(1 for v in nets if v > 0) / n, 4) if n else None,
        "max_drawdown": _metrics.max_drawdown(svals),
        "mean_session_net": _r(mean),
        "sd_session_net": _r(sd),
        "session_net_ci": [_r(ci[0]), _r(ci[1])] if ci else None,
        "session_net_ci_excludes_zero": excludes,
        "sessions_to_exclude_zero": 0
        if excludes
        else _stats.sessions_to_exclude_zero(mean, sd, len(svals), level=level),
        "entries_per_session": _r(n / len(sv)) if sv else None,
        "net_per_entry": _r(sum(nets) / n) if n else None,
        "capital": _r(risk) if known else None,
        "capital_known_trades": len(known),
        "net_per_100_risk": _r(100.0 * sum(v for v, _ in known) / risk) if risk else None,
        "trade_nets": [_r(v) for v in nets],
        "session_nets": [[s, _r(v)] for s, v in sv.items()],
    }


def pair_with_control(
    arm_records: list[dict],
    control_records: list[dict],
    *,
    schema: str,
    level: float = LEVEL,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict | None:
    """An arm's closed records against its control's, by this module's pairing rule (see the
    module docstring): keyed on the day for a same-day schema and on `entry_session` otherwise,
    common sessions inside the overlap of the two windows, one-side-only sessions counted. The
    advisor's verdicts call this too, so a verdict and the compare page cannot pair differently.
    None when either side has no dated rows."""
    pair_on = "session" if schema in SAME_DAY_SCHEMAS else "entry_session"

    def rekeyed(records: list[dict]) -> list[dict]:
        return [
            {"pair": r.get(pair_on) or r.get("session"), "net_pnl": r["net_pnl"], "capital": r.get("capital")}
            for r in records
            if r.get("net_pnl") is not None  # n/r rows are unscored, never a flat trade
        ]

    arm_rows, control_rows = rekeyed(arm_records), rekeyed(control_records)
    mine = _stats.session_values(arm_rows, session_key="pair")
    theirs = _stats.session_values(control_rows, session_key="pair")
    if not mine or not theirs:
        return None
    lo = max(next(iter(mine)), next(iter(theirs)))
    hi = min(next(reversed(mine)), next(reversed(theirs)))
    window = [s for s in sorted(set(mine) | set(theirs)) if lo <= s <= hi]
    vs = _stats.paired_comparison(
        mine, theirs, fill=None, sessions=window, level=level, resamples=resamples, seed=seed
    )
    vs["paired_on"] = pair_on
    vs["arm_only_sessions"] = sum(1 for s in window if s in mine and s not in theirs)
    vs["control_only_sessions"] = sum(1 for s in window if s in theirs and s not in mine)

    # The same pair on size-normalised session figures: same key, same window, same rule. A side
    # with no recorded capital on any shared session has no risk-normalised pair (None, not zero).
    for name, per in (("per_entry", "entry"), ("per_100_risk", "risk")):
        a = normalised_session_values(arm_rows, per, session_key="pair")
        c = normalised_session_values(control_rows, per, session_key="pair")
        shared = [s for s in window if s in a and s in c]
        vs[name] = (
            _stats.paired_comparison(
                a, c, fill=None, sessions=window, level=level, resamples=resamples, seed=seed
            )
            if shared
            else None
        )
    return vs


def module_comparison(
    module: str,
    *,
    db_path: Path | None = None,
    session: str | None = None,
    scope: str = "era",
    experiments: Mapping[tuple[str, str], dict] | None = None,
    level: float = LEVEL,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict:
    """Every arm of one module in scope, each with its own reading and its paired difference with
    its control. `status` is `ok`, `absent` (no ledger here) or `failed` (with `reason`)."""
    schema = MODULE_SCHEMAS[module]
    session = session or _dt.date.today().isoformat()
    path = Path(db_path) if db_path else _home.data_dir(module) / "paper_trades.db"
    out: dict[str, Any] = {"module": module, "trade_schema": schema, "status": "ok", "reason": None}
    if not path.exists():
        return {**out, "status": "absent", "reason": "no paper ledger", "era": None, "arms": []}
    try:
        conn = _ledgers.connect_ro(path)
        try:
            records = _ledgers.READERS[schema](conn, end=session)
            breaks = read_breaks(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {**out, "status": "failed", "reason": f"{type(exc).__name__}: {exc}", "era": None, "arms": []}
    records = [r for r in records if r.get("session") and r["session"] <= session]

    era = era_window(module, breaks, session)
    era["era_stamp"] = None
    if schema in _ledgers.ERA_SCHEMAS:
        era["era_stamp"] = current_era_stamp(breaks, session)
    out["era"] = era
    out["scope"] = scope

    def in_scope(r: dict) -> bool:
        if scope != "era":
            return True
        if schema in _ledgers.ERA_SCHEMAS:
            return era["era_stamp"] is not None and r.get("era") == era["era_stamp"]
        start = era["arm_starts"].get(r["arm"]) or era["start"]
        return start is None or r["session"] >= start

    by_arm: dict[str, list[dict]] = {}
    for r in records:
        if in_scope(r):
            by_arm.setdefault(r["arm"], []).append(r)

    experiments = experiments if experiments is not None else {}
    names = sorted(by_arm, key=_arm_order)
    readings = {a: arm_reading(by_arm[a], level=level, resamples=resamples, seed=seed) for a in names}
    arms = []
    for a in names:
        reading = readings[a]
        ctl = control_for(module, a, names, experiments)
        tag = a.rsplit(":", 1)[0] if (module == "earnings" and a.count(":") >= 2) else a
        exp = experiments.get((module, tag)) if a.startswith("advised:") else None
        start, start_reason = None, "first_row"
        if scope == "era" and schema in _ledgers.ERA_SCHEMAS:
            start_reason = "era_stamp"
        elif scope == "era" and a in era["arm_starts"]:
            start, start_reason = era["arm_starts"][a], "arm_added"
        elif scope == "era" and era["start"]:
            start, start_reason = era["start"], "era"
        entry: dict[str, Any] = {
            "arm": a,
            "advised": a.startswith("advised:"),
            "experiment_id": exp["id"] if exp else None,
            "experiment_status": exp["status"] if exp else None,
            "start": start,
            "start_reason": start_reason,
            **reading,
            "control": ctl,
            "vs_control": None,
        }
        if ctl is not None:
            entry["vs_control"] = pair_with_control(
                by_arm[a], by_arm[ctl], schema=schema, level=level, resamples=resamples, seed=seed
            )
        arms.append(entry)
    out["arms"] = arms
    return out


def compare(
    modules: Iterable[str] | None = None,
    *,
    session: str | None = None,
    scope: str = "era",
    db_paths: Mapping[str, Path] | None = None,
    advisor_db: Path | None = None,
    generated_at: str | None = None,
) -> dict:
    """The whole document the bridge prints."""
    if scope not in ("era", "all"):
        raise ValueError(f"compare: scope must be 'era' or 'all', not {scope!r}")
    session = session or _dt.date.today().isoformat()
    experiments = experiments_index(advisor_db)
    db_paths = db_paths or {}
    wanted = list(modules) if modules else list(MODULE_SCHEMAS)
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": generated_at or _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "session": session,
        "scope": scope,
        "level": LEVEL,
        "resamples": RESAMPLES,
        "seed": SEED,
        "pairing": "common sessions inside the overlap of the two windows",
        "costs_note": COSTS_NOTE,
        "modules": [
            module_comparison(
                m, db_path=db_paths.get(m), session=session, scope=scope, experiments=experiments
            )
            for m in wanted
        ],
    }


__all__ = [
    "COSTS_NOTE",
    "MODULE_SCHEMAS",
    "NON_BOUNDING_KEYS",
    "SCHEMA_VERSION",
    "arm_reading",
    "compare",
    "control_for",
    "current_era_stamp",
    "era_window",
    "experiments_index",
    "module_comparison",
    "normalised_session_values",
    "pair_with_control",
    "read_breaks",
]
