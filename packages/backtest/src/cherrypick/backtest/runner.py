"""Run a module's own rules over the vendor store's sessions: flies and MEIC.

Each session is replayed by the module itself (`python -m cherrypick.flies.cli replay`,
`python -m cherrypick.meic.replay run`, a subprocess: this package imports no strategy module), into
a scratch ledger of its own, settled at that day's close from the store. The results are read back
through `cherrypick.core.ledgers`, the one home of every schema's net rule, so a backtest's net is the
same arithmetic as paper's. What differs per module (schema, fill model, config files, the spec a
trial is keyed on, the replay command) is one `Module` entry in `MODULES`.

What may run, and when (declared 2026-10-17):

- **`validation`** (sessions from the recorder's first, 2026-10-13): always. These days are already
  seen; they are what the vendor data is checked against, and they count as no trial.
- **`design`**: only once validation has unlocked history for this store's cadence
  (`validate.UNLOCK_SESSIONS`). Every (spec, arm) evaluated here is a trial (`results.trials`).
- **`holdout`**: as design, and only with `spend_holdout`, once per spec, ever.

The fill model is the module's own (flies paper's legged fill rule, applied by its own tick); a
result carries that name, and other fill models arrive as flies options, never as a second copy of
the rule here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cherrypick.core import compare as core_compare
from cherrypick.core import home as _home
from cherrypick.core import ledgers as core_ledgers

from . import paths, results, stats, store

MODULE = "flies"
SCHEMA = "fly_book"
FILL_MODEL = "flies-paper-legged"
VALIDATION_START = "2026-10-13"
WINDOWS = ("validation", "design", "holdout")


class RunRefused(ValueError):
    pass


def _flies_config_text() -> tuple[str, Path]:
    """The flies config the replay subprocess will read: FLIES_CONFIG, else the home's. A backtest of
    an arm is a statement about declared parameters, so the package example is not enough."""
    import os

    env = os.environ.get("FLIES_CONFIG")
    path = Path(env) if env else _home.config_path("flies")
    if not path.exists():
        raise RunRefused(f"no flies config at {path}: a backtest runs the operator's declared arms")
    return path.read_text(encoding="utf-8"), path


def spec_for(config: dict, arm: str, *, cadence: int) -> dict:
    arms = config.get("arms") or {}
    if arm not in arms or not isinstance(arms[arm], dict):
        raise RunRefused(f"flies has no arm {arm!r}")
    return {
        "module": MODULE,
        "arm": arm,
        "defaults": config.get("defaults") or {},
        "arm_params": arms[arm],
        "symbols": config.get("symbols"),
        "cadence": cadence,
        "fill_model": FILL_MODEL,
    }


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _code_commit() -> str | None:
    root = Path(__file__).resolve().parents[5]
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True
        ).stdout
        return head.strip() + ("+dirty" if dirty.strip() else "") if head.strip() else None
    except OSError:
        return None


def unlocked(settings: dict, symbol: str, cadence: int, *, module: str = MODULE) -> bool:
    """Whether the newest validation report for this module and store says history may run. Each module
    validates its own replay (a flies report unlocks nothing for MEIC); a report from before the module
    was recorded is flies'."""
    vendor_db = str(store.db_path(settings, symbol, cadence))
    reports = sorted((paths.results_dir() / "validation").glob("*.json"), key=lambda p: p.stat().st_mtime)
    for p in reversed(reports):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("vendor_db") == vendor_db and doc.get("module", MODULE) == module:
            return bool((doc.get("decisions") or {}).get("unlocks_history"))
    return False


def sessions_in(
    conn: sqlite3.Connection, symbol: str, start: str, end: str
) -> list[tuple[str, float | None]]:
    """Complete vendor sessions in [start, end] with that day's close (None when the store has none)."""
    return [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT v.trade_date, c.close FROM vendor_sessions v LEFT JOIN daily_closes c "
            "ON c.symbol = v.symbol AND c.trade_date = v.trade_date "
            "WHERE v.symbol = ? AND v.status = ? AND v.trade_date BETWEEN ? AND ? ORDER BY v.trade_date",
            (symbol.upper(), store.COMPLETE, start, end),
        )
    ]


def _fingerprint(conn, symbol: str, days: list[str]) -> str:
    h = hashlib.sha256()
    for d in days:
        row = conn.execute(
            "SELECT trade_date, samples, quotes, profiles, fetched_at FROM vendor_sessions "
            "WHERE symbol = ? AND trade_date = ?",
            (symbol.upper(), d),
        ).fetchone()
        h.update(json.dumps(list(row)).encode())
    return h.hexdigest()[:16]


def _replay(day: str, out: Path, arms: list[str], history_db: Path, settle: float, config: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.flies.cli", "--config", str(config), "replay", "--date", day,
         "--out", str(out),
         "--arms", ",".join(arms), "--history-db", str(history_db), "--settle-price", repr(settle)],
        capture_output=True, text=True, timeout=3600,
    )  # fmt: skip
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-600:]}


def _ledger_rows(path: Path, schema: str = SCHEMA) -> list[dict]:
    """A replayed session's closed rows, through the schema's one reader."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return core_ledgers.READERS[schema](conn)
    finally:
        conn.close()


# ----------------------------------------------------------------------------------------------- MEIC

MEIC_FILL_MODEL = "meic-paper-mid-haircut"


def _meic_config_texts() -> tuple[dict[str, str], dict[str, Path]]:
    """MEIC's base config and arm registry, as the replay subprocess will read them: `$MEIC_CONFIG` /
    `$MEIC_RISK_CONFIG`, else the home's `meic.json` / `meic.risk.json`. A backtest of an arm is a
    statement about the operator's declared arms, so the package examples are not enough."""
    paths_ = {
        "base": Path(os.environ["MEIC_CONFIG"])
        if os.environ.get("MEIC_CONFIG")
        else _home.config_path("meic"),
        "registry": Path(os.environ["MEIC_RISK_CONFIG"])
        if os.environ.get("MEIC_RISK_CONFIG")
        else _home.config_dir() / "meic.risk.json",
    }
    for name, path in paths_.items():
        if not path.exists():
            raise RunRefused(f"no MEIC {name} config at {path}: a backtest runs the operator's declared arms")
    return {k: p.read_text(encoding="utf-8") for k, p in paths_.items()}, paths_


def meic_spec_for(config: dict, arm: str, *, cadence: int) -> dict:
    """What decides an arm's trades: its params merged over the base (MEIC's flat overlay), comments
    dropped, so a change to a base key the arm inherits is a new trial and a reworded note is not."""
    registry = config["registry"].get("profiles", config["registry"])
    profile = registry.get(arm)
    if not isinstance(profile, dict):
        raise RunRefused(f"MEIC has no arm {arm!r}")
    merged = {**config["base"], **profile}
    return {
        "module": "meic",
        "arm": arm,
        "params": {k: v for k, v in merged.items() if not str(k).startswith("_")},
        "cadence": cadence,
        "fill_model": MEIC_FILL_MODEL,
    }


def _meic_replay(day: str, out: Path, arms: list[str], history_db: Path, settle: float, config: Path) -> dict:
    """`python -m cherrypick.meic.replay run` against this run's snapshot of both config files."""
    env = {
        **os.environ,
        "MEIC_CONFIG": str(config),
        "MEIC_RISK_CONFIG": str(config.parent / "meic-risk.json"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "cherrypick.meic.replay", "run", "--date", day, "--out", str(out),
         "--arms", ",".join(arms), "--history-db", str(history_db), "--settle-price", repr(settle)],
        capture_output=True, text=True, timeout=3600, env=env,
    )  # fmt: skip
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (proc.stderr or proc.stdout)[-600:]}


@dataclass(frozen=True)
class Module:
    """Everything the runner needs to know about one strategy module, and nothing it computes."""

    name: str
    schema: str
    fill_model: str
    load: Callable[[], tuple[dict, dict[str, Path]]]  # (config, the source paths)
    snapshot: Callable[[dict, Path], Path]  # write the config beside the ledgers; the path to hand on
    spec_for: Callable[..., dict]
    replay: Callable[..., dict]


def _flies_load() -> tuple[dict, dict[str, Path]]:
    text, path = _flies_config_text()
    return json.loads(text), {"flies": path}


def _flies_snapshot(config: dict, ledger_dir: Path) -> Path:
    path = ledger_dir / "flies-config.json"
    path.write_text(json.dumps(config, indent=1), encoding="utf-8")
    return path


def _meic_load() -> tuple[dict, dict[str, Path]]:
    texts, paths_ = _meic_config_texts()
    return {k: json.loads(v) for k, v in texts.items()}, paths_


def _meic_snapshot(config: dict, ledger_dir: Path) -> Path:
    (ledger_dir / "meic-risk.json").write_text(json.dumps(config["registry"], indent=1), encoding="utf-8")
    path = ledger_dir / "meic-config.json"
    path.write_text(json.dumps(config["base"], indent=1), encoding="utf-8")
    return path


MODULES = {
    "flies": Module("flies", SCHEMA, FILL_MODEL, _flies_load, _flies_snapshot, spec_for, _replay),
    "meic": Module(
        "meic", "meic_ic", MEIC_FILL_MODEL, _meic_load, _meic_snapshot, meic_spec_for, _meic_replay
    ),
}


def _window(settings: dict, name: str) -> tuple[str, str]:
    if name == "validation":
        return VALIDATION_START, "9999-12-31"
    return tuple(settings["windows"][name])


def run(
    settings: dict,
    arms: list[str],
    window: str,
    *,
    symbol: str = "SPX",
    cadence: int | None = None,
    start: str | None = None,
    end: str | None = None,
    spend_holdout: bool = False,
    workers: int = 4,
    extra_arms: dict[str, dict] | None = None,
    provisional: bool = False,
    module: str = MODULE,
    replay=None,
    read_rows=None,
    log=print,
) -> dict:
    """`extra_arms` adds config-defined arms (`advised:<name>`: a base arm's params with an
    overlay; flies only) for this run only; every run replays from its own snapshot of the module's
    config, kept beside its ledgers, so what ran is on disk exactly.

    `provisional` (design window only) runs while history is still locked, to look early. It is
    no evidence and is kept apart from evidence: it records no trial, cannot take advised arms or
    the holdout, and its run and report are marked provisional, which every reader shows."""
    if module not in MODULES:
        raise RunRefused(f"module must be one of {sorted(MODULES)}")
    mod = MODULES[module]
    replay = replay or mod.replay
    read_rows = read_rows or (lambda path: _ledger_rows(path, mod.schema))
    if window not in WINDOWS:
        raise RunRefused(f"window must be one of {WINDOWS}")
    if provisional and (window != "design" or extra_arms):
        raise RunRefused("a provisional run is the design window only, and never the advisor's specs")
    cadence = store.cadence_of(settings, cadence)
    arms = list(dict.fromkeys(["control", *arms]))  # control always runs: every result is paired with it
    config, config_paths = mod.load()
    if extra_arms:
        if module != "flies":
            raise RunRefused("advised arms are flies' (the advisor's backtest specs)")
        if window != "design":
            raise RunRefused("advised arms run over the design window only")
        config.setdefault("arms", {}).update(extra_arms)
        arms = [*arms, *extra_arms]
    specs = {a: mod.spec_for(config, a, cadence=cadence) for a in arms}
    hashes = {a: spec_hash(s) for a, s in specs.items()}
    w_lo, w_hi = _window(settings, window)
    lo, hi = max(w_lo, start or w_lo), min(w_hi, end or w_hi)
    if window != "validation" and not provisional and not unlocked(settings, symbol, cadence, module=module):
        raise RunRefused(
            f"history is locked for {module} on {symbol} at {cadence}s: validation has not unlocked it "
            f"(python -m cherrypick.backtest validate --module {module})"
        )
    rconn = results.connect()
    if window == "holdout":
        if not spend_holdout:
            raise RunRefused("the holdout is spent once per spec: pass --spend-holdout to spend it")
        spent = [a for a in arms if results.holdout_spent(rconn, mod.name, hashes[a])]
        if spent:
            raise RunRefused(f"the holdout is already spent for {spent}")
    history_db = store.db_path(settings, symbol, cadence)
    vconn = store.connect(settings, symbol, cadence)
    try:
        days = sessions_in(vconn, symbol, lo, hi)
        fingerprint = _fingerprint(vconn, symbol, [d for d, _ in days])
    finally:
        vconn.close()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    ledger_dir = paths.results_dir() / "runs" / run_id
    ledger_dir.mkdir(parents=True, exist_ok=True)
    snapshot = mod.snapshot(config, ledger_dir)
    refused: dict[str, str] = {}
    todo = []
    for day, close in days:
        if close is None:
            refused[day] = "no_settle_price"
        else:
            todo.append((day, close))

    def one(item):
        day, close = item
        try:
            out = replay(day, ledger_dir / f"{day}.db", arms, history_db, close, snapshot)
            rows = read_rows(ledger_dir / f"{day}.db") if out.get("ok") else None
        except Exception as exc:  # noqa: BLE001 -- one bad session is that session's refusal, never the run's
            return day, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, None
        return day, out, rows

    per_arm: dict[str, dict[str, dict]] = {a: {} for a in arms}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for day, out, rows in pool.map(one, todo):
            if not out.get("ok"):
                refused[day] = str(out.get("error"))[:200]
                continue
            for arm in arms:
                mine = [r for r in rows if r["arm"] == arm and r["session"] == day]
                per_arm[arm][day] = {
                    "net": round(sum(r["net_pnl"] or 0.0 for r in mine), 2),
                    "gross": round(sum(r["gross_pnl"] or 0.0 for r in mine), 2),
                    "fees": round(sum(r["cost"] or 0.0 for r in mine), 2),
                    "trades": len(mine),
                }
            log(f"{day} ok")
    report = _summarise(rconn, run_id, arms, specs, hashes, per_arm, window, provisional=provisional, mod=mod)
    report.update(
        {
            "run_id": run_id,
            "module": mod.name,
            "window": window,
            "provisional": provisional,
            "span": [lo, hi],
            "cadence": cadence,
            "fill_model": mod.fill_model,
            "config_files": {k: str(p) for k, p in config_paths.items()},
            "config_snapshot": str(snapshot),
            "code_commit": _code_commit(),
            "data_fingerprint": fingerprint,
            "sessions_requested": len(days),
            "refused": refused,
        }
    )
    if mod.name == "flies":  # the keys every flies report has carried
        report["flies_config"] = str(config_paths["flies"])
        report["flies_config_snapshot"] = str(snapshot)
    rconn.execute(
        "INSERT INTO runs (run_id, created_at, module, arms, window_name, start, end, cadence, code_commit, "
        "data_fingerprint, fill_model, status, sessions, refused, report) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, time.time(), mod.name, json.dumps(arms), window, lo, hi, cadence,
            report["code_commit"], fingerprint, mod.fill_model, "provisional" if provisional else "done",
            len(per_arm["control"]),
            json.dumps(refused), json.dumps(report, default=str),
        ),
    )  # fmt: skip
    for arm in arms:
        for day, v in per_arm[arm].items():
            rconn.execute(
                "INSERT INTO session_results (run_id, arm, spec_hash, session, net, gross, fees, trades) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run_id, arm, hashes[arm], day, v["net"], v["gross"], v["fees"], v["trades"]),
            )
        if window == "holdout":
            results.spend_holdout(rconn, module=mod.name, spec_hash=hashes[arm], arm=arm, run_id=run_id)
    rconn.commit()
    rconn.close()
    out_path = paths.results_dir() / "runs" / f"{run_id}.json"
    out_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["written"] = str(out_path)
    return report


def _summarise(rconn, run_id, arms, specs, hashes, per_arm, window, *, provisional=False, mod=None) -> dict:
    """Per arm: the statistics at the module's trial count, and the pairing with control. A
    provisional run records no trial (it is a look, not an evaluation), but its own statistics
    are still deflated by the trials already on record."""
    mod = mod or MODULES[MODULE]
    if window == "design" and not provisional:
        for arm in arms:
            values = [v["net"] for _, v in sorted(per_arm[arm].items())]
            m = stats.moments(values)
            results.record_trial(
                rconn, module=mod.name, spec_hash=hashes[arm], arm=arm, spec=specs[arm], run_id=run_id,
                sessions=len(values), sharpe=None if m is None else m["sharpe"],
            )  # fmt: skip
    trials = max(1, results.trial_count(rconn, mod.name))
    sharpes = results.trial_sharpes(rconn, mod.name)
    control = [{"session": d, "net_pnl": v["net"]} for d, v in per_arm["control"].items()]
    out = {"trials_for_module": trials, "arms": {}}
    if provisional:
        out["provisional_note"] = (
            "PROVISIONAL: history is not yet unlocked by validation. Not a trial, not evidence, "
            "and never shown to the advisor."
        )
    for arm in arms:
        values = [v["net"] for _, v in sorted(per_arm[arm].items())]
        entry = {
            "spec_hash": hashes[arm],
            "summary": stats.summary(
                values, trials=trials if window != "validation" else 1, trial_sharpes=sharpes
            ),
            "fees": round(sum(v["fees"] for v in per_arm[arm].values()), 2),
            "trades": sum(v["trades"] for v in per_arm[arm].values()),
        }
        if arm != "control":
            mine = [{"session": d, "net_pnl": v["net"]} for d, v in per_arm[arm].items()]
            entry["vs_control"] = core_compare.pair_with_control(mine, control, schema=mod.schema)
            # A book that never diverged measured nothing (the advisor's verdict rule): an arm whose
            # every session matches control's to the cent, trade count included, is no evidence.
            entry["no_exposure"] = bool(per_arm[arm]) and all(
                per_arm["control"].get(d) == v for d, v in per_arm[arm].items()
            )
        out["arms"][arm] = entry
    return out
