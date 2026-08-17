"""Config loading and path resolution for cherrypick.

All paths are derived from this file's location or from config values — never hardcoded
absolute paths (a portability guardrail inherited from both sibling modules).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

# cherrypick runtime root — where config.json, logs/, and state/ live. In a source checkout that is the
# repo root; this module sits at <root>/src/cherrypick/orchestrator/config.py, so the root is 3 parents
# up. An installed copy (no repo root) sets CHERRYPICK_HOME to its runtime dir instead.
# The per-user runtime home for an installed copy (config.json, logs/, state/, dashboard.html, modules/).
_USER_HOME = Path.home() / ".cherrypick"


def _source_root() -> Path:
    """The **source anchor** for resolving a module's relative `path` in config (e.g. `../meic`) — the
    orchestrator checkout dir (this file sits at <root>/src/cherrypick/orchestrator/config.py, so it is
    3 parents up). This stays tied to the checkout even though runtime files (config/state/dashboard/
    logs) now live under the per-user home, so an in-place `path: ../meic` keeps resolving into the repo
    regardless of where config.json physically lives. CHERRYPICK_HOME overrides it for an installed copy
    (where modules come from MODULES_HOME, not a relative checkout)."""
    env = os.environ.get("CHERRYPICK_HOME")
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "run.py").exists() or (repo_root / "pyproject.toml").exists():
        return repo_root
    return _USER_HOME


def _logs_home() -> Path:
    """Where cherrypick writes its logs. Always the per-user home (~/.cherrypick/logs), independent of
    ROOT — so log output never lands inside a source checkout and its location is stable and
    user-scoped regardless of how cherrypick is launched. Delegates to the suite-wide resolver, so
    CHERRYPICK_HOME relocates it uniformly with every other package's logs."""
    return _home.logs_dir()


# ROOT is the source anchor for relative module paths (see _source_root); the runtime files themselves
# — config.json, state/, dashboard.html, logs/ — all live under the per-user home now, so nothing runtime
# is written into the checkout.
ROOT = _source_root()
CONFIG_PATH = _home.config_path()
LOGS_DIR = _logs_home()
STATE_DIR = _home.state_dir()

# Where `cherrypick install` materializes module checkouts when a module declares no explicit `path`.
# Precedence: CHERRYPICK_MODULES_HOME (test/override) → CHERRYPICK_HOME/modules → ~/.cherrypick/modules.
# Kept independent of ROOT (via the shared resolver) so a source checkout still parks modules in the user
# dir rather than nesting them (and their runtime data — e.g. Earnings' multi-GB Dolt store) in the repo.
MODULES_HOME = _home.modules_dir()


LEGACY_CONFIG_PATH = ROOT / "config.json"


def effective_config_path() -> Path:
    """The config file to read: the per-user home config (`~/.cherrypick/config.json`) once it exists,
    otherwise a legacy in-repo `config.json` (a source checkout that predates the move). A pure lookup —
    it never writes, so importing/reading has no side effects and test runs can't pollute the real home;
    the actual file move into the home is an explicit step (`cherrypick migrate-home`). Falls back to the
    home path for the 'not found' message when neither exists."""
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    return LEGACY_CONFIG_PATH if LEGACY_CONFIG_PATH.exists() else CONFIG_PATH


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the cherrypick config (home config, or a legacy in-repo one until
    migrated — see :func:`effective_config_path`)."""
    cfg_path = path or effective_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"cherrypick config not found at {cfg_path}. Copy config.example.json there to create it."
        )
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if "modules" not in cfg:
        raise ValueError("config.json missing 'modules' section")
    return cfg


def module_dirname(module_cfg: dict[str, Any], name: str | None = None) -> str:
    """Checkout directory name under MODULES_HOME: the repo basename
    (…/cherrypick-meic.git → cherrypick-meic) when a 'repo' is configured, else the module's key."""
    repo = module_cfg.get("repo")
    if repo:
        stem = str(repo).rstrip("/").rsplit("/", 1)[-1]
        return stem[:-4] if stem.endswith(".git") else stem
    if name:
        return name
    raise ValueError("module config needs a 'repo', a 'path', or a name to locate its checkout")


def module_root(module_cfg: dict[str, Any], name: str | None = None) -> Path:
    """Resolve a module's on-disk root.

    An explicit 'path' (absolute, or relative to cherrypick ROOT) always wins — the dev override for a
    working checkout. With no 'path', the module lives at its managed install location
    MODULES_HOME/<dirname> (see module_dirname), which is where `cherrypick install` clones it.
    """
    raw = module_cfg.get("path")
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        return p.resolve()
    return (MODULES_HOME / module_dirname(module_cfg, name)).resolve()


def portable_path(p: Any) -> str:
    """Render a filesystem path for display without leaking a drive letter, username, or absolute home
    prefix — the suite guardrail forbids absolute paths on any surface (dashboard, doctor, section cards).
    Collapse the user home to ``~``; else show it relative to the cherrypick source root (ROOT); else
    just the final path component.

    The ROOT-relative leg only applies when the path is actually *under* ROOT. `os.path.relpath` will
    happily walk up out of ROOT and back down (`../../../tmp/...`), which keeps every original segment
    and defeats the whole point of this function. That escape never fired on Windows — a different
    drive raises ValueError — so it stayed invisible until orchestrator CI first ran on Linux.
    """
    path = Path(p)
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        pass
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def paper_db_path(module_cfg: dict[str, Any], name: str | None = None) -> Path:
    """Resolve a module's paper-trades DB file. `paper.paper_db` may be:
      - absolute (used as-is);
      - `~`- or env-prefixed — expanded, so a module whose data lives in the managed home can be pointed
        at e.g. `~/.cherrypick/data/meic/paper_trades.db` without a hardcoded machine path; or
      - relative — resolved against the module checkout root (the historical default).
    Mirrors `dolt_service.data_dir` resolution. One source of truth so every read surface (report,
    reconcile, calibrate, dashboard) and the watchdog freshness check agree on which file the module
    actually writes — a mismatch silently blinds the orchestrator to a module's paper data.
    """
    rel = (module_cfg.get("paper", {}) or {}).get("paper_db", "data/paper_trades.db")
    p = Path(os.path.expandvars(os.path.expanduser(str(rel))))
    if p.is_absolute():
        return p.resolve()
    return (module_root(module_cfg, name) / p).resolve()


# Known-module onboarding defaults (the redesign's step 6): applied when a config omits the
# key, so an existing machine-local config needs ZERO broker keys for connect/account to work.
# Config always wins when present; a genuinely new module still declares its keys in config.
KNOWN_MODULE_DEFAULTS: dict[str, dict[str, Any]] = {
    "meic": {"keyring_service": "meicagent", "broker_tool": ["-m", "cherrypick.meic.tt"]},
    "earnings": {"keyring_service": "earningsagent", "broker_tool": ["-m", "cherrypick.earnings.tt"]},
    "flies": {"keyring_service": "fliesagent", "broker_tool": ["-m", "cherrypick.flies.broker_cli"]},
}


def _module_default(name: str | None, key: str) -> Any:
    return (KNOWN_MODULE_DEFAULTS.get(name or "") or {}).get(key)


def broker_tool(module_cfg: dict[str, Any], name: str | None = None) -> list[str]:
    """The module's broker/credential CLI as an argv prefix, relative to its root. Resolution:
    explicit config -> known-module default (by name) -> a last-resort `-m cherrypick.<name>.tt`.
    Used by connect/account/reconcile so onboarding and the isolation guard drive every module
    through config-declared argv, like everything else."""
    fallback = ["-m", f"cherrypick.{name}.tt"] if name else []
    return list(module_cfg.get("broker_tool") or _module_default(name, "broker_tool") or fallback)


def module_keyring_service(module_cfg: dict[str, Any], name: str | None = None) -> str | None:
    """The module's keyring service: explicit config -> known-module default -> None (no
    account-selection surface for that module). An EXPLICIT null in config disables the
    default -- the escape hatch for deliberately opting a known module out."""
    if "keyring_service" in module_cfg:
        return module_cfg["keyring_service"] or None
    return _module_default(name, "keyring_service")


def live_db_path(module_cfg: dict[str, Any], name: str | None = None) -> Path | None:
    """Resolve a module's LIVE-trades DB from its top-level `live_db` key, or None when the module
    declares none (flies never will -- it is paper by design). Same resolution rules as
    `paper_db_path` (absolute / ~-and-env expanded / module-root relative). Deliberately a separate
    key and a separate resolver: the paper path feeds `report.run` and everything promotion reads;
    this one feeds only the explicitly live-tagged surfaces (`report.live_run`)."""
    rel = module_cfg.get("live_db")
    if not rel:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(str(rel))))
    if p.is_absolute():
        return p.resolve()
    return (module_root(module_cfg, name) / p).resolve()


def sla_state_files(name: str, mcfg: dict) -> tuple[Path, Path]:
    """The (entry, exit) SLA heartbeat paths for a `cherrypick_scheduled` module.

    Derived from the module NAME, not hardcoded. These used to be spelled `earnings_entry.last.json`
    literally at both read sites, which was invisible while Earnings was the only scheduled module and
    silently wrong the moment a second one appeared — the dashboard showed Earnings' SLA as the other
    module's, and the watchdog raised a CRITICAL named after Earnings for it. `paper.sla_state_prefix`
    overrides the derivation for a module whose heartbeat files are named something else.
    """
    prefix = mcfg.get("paper", {}).get("sla_state_prefix", name)
    return (STATE_DIR / f"{prefix}_entry.last.json", STATE_DIR / f"{prefix}_exit.last.json")


def module_logs_dir(name: str) -> Path:
    """A module's logs home: `LOGS_DIR/<name>` (e.g. ~/.cherrypick/logs/meic) — the location the module's
    own `paths.logs_dir()` writes to by the shared convention. Used to find each module's log tails and
    the per-module `paper-eod-<day>.md` files the EOD digest links to. Both sides derive it the same way
    from the (CHERRYPICK_HOME-aware) logs home, so they agree without the orchestrator importing the
    module. A module-private override (MEIC_LOGS_DIR/EARNINGS_LOGS_DIR) is a test/machine escape hatch
    only; in normal operation the convention holds."""
    return LOGS_DIR / name


def live_trading_enabled(module_cfg_doc: dict[str, Any]) -> bool:
    """Whether a module's OWN config document (not the orchestrator's) has live trading switched
    on, across the suite's two conventions: a top-level `enable_live_trading` bool (meic, earnings)
    or a nested `live.enabled` bool (flies — the only module armed per-day rather than by a static
    flag). Checking only the first convention left every flies-specific safety surface (the Live Ops
    card, `cherrypick account`'s live warning) blind to flies actually being armed — it always read
    as PAPER ONLY regardless of `live.enabled`. Either flag being true means true; a future module
    could use either shape without a code change here."""
    if bool(module_cfg_doc.get("enable_live_trading", False)):
        return True
    live = module_cfg_doc.get("live")
    return bool(isinstance(live, dict) and live.get("enabled", False))


def enabled_modules(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {name: module_cfg} for modules with enabled=true."""
    return {name: mcfg for name, mcfg in cfg.get("modules", {}).items() if mcfg.get("enabled", False)}


def enabled_services(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Long-running background daemons the orchestrator keeps alive (top-level `services`): started at
    `install`, kept up by the watchdog, and located by `path`/`repo` like modules. Each declares
    `status_argv` (prints `{"running": bool}`), `start_argv`, and `auto_restart`. Distinct from the
    `modules` registry (paper pipelines) — a service has no paper DB or schedule of its own, e.g. the gex
    spot-trail recorder that runs alongside the streamer."""
    return [s for s in (cfg.get("services") or []) if s.get("enabled") and s.get("id")]


def review_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved cross-module end-of-day review config (packages/review). ON by default.

    Two passes, because the modules do not all finish at the same time. `provisional_at` runs after
    the 0DTE modules settle and captures MEIC and flies complete, with earnings shown as carried
    overnight risk; `final_at` runs the next morning once earnings has closed, finalises the prior
    session, and is the only status the narrative is ever written against — which is what lets that
    narrative be written once and frozen.

    Read-only over every module's ledger and writes only into review's own home, so neither pass can
    affect a loop; a failed pass costs a report, never a trade.
    """
    rv = cfg.get("review", {}) or {}
    return {
        "enabled": rv.get("enabled", True),
        # ET, box-local like the modules' own entry/exit times. 16:30 is after the 0DTE settles;
        # 10:15 is after earnings' 09:45 close window has had time to run.
        "provisional_at": rv.get("provisional_at", "16:30"),
        "final_at": rv.get("final_at", "10:15"),
        # The narrative runs after the final pass, never with it: it must only ever see a finalised
        # session. OFF by default because it shells out to Claude Code, which is a dependency the
        # suite does not otherwise have -- turn it on once `claude` is on PATH.
        "narrative": rv.get("narrative", False),
        "narrative_at": rv.get("narrative_at", "10:45"),
        "file_issues": rv.get("file_issues", False),
    }


def morning_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved pre-open market-overview config (packages/overview). ON by default for the pack,
    OFF for the narrative, mirroring the review's posture.

    Two jobs: `factpack_at` builds the deterministic morning pack (a pure stream-cache + GEX
    consumer — no credential, no network, so it is as safe to run unattended as the review), and
    `narrative_at` runs scripts/morning_narrative.py against it. The narrative shells out to Claude
    Code and — unlike the EOD narrative — is allowed web lookups for the macro calendar, which is
    one more reason it lives outside every package and stays off by default.
    """
    mv = cfg.get("morning", {}) or {}
    return {
        "enabled": mv.get("enabled", True),
        # ET, box-local. 08:30 leaves the pack a full hour before the open; the narrative follows
        # at 09:00 so a human reading pre-open gets facts even when the AI step fails or is off.
        "factpack_at": mv.get("factpack_at", "08:30"),
        "narrative": mv.get("narrative", False),
        "narrative_at": mv.get("narrative_at", "09:00"),
    }


def advisor_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved AI-advisor scheduling (packages/advisor + scripts/advisor_checkpoint.py). OFF by default.

    Eight daily slots: seven light intraday checkpoints on a cheap model, and one deep post-close run
    on the strong one. The deep slot follows the review's provisional pass (16:30) so it can read
    that fact set, and it is the slot that issues the next session's advice.

    Off by default twice over, because two independent things have to be true before anything
    happens: the suite has to schedule the advisor (this block), and each module has to declare an
    `advice` block of its own saying which parameters it will accept advice about and between which
    values. Neither implies the other.

    Model names live here and travel on argv. No model id appears anywhere in this suite's code, so
    changing which model runs a slot is a config edit.

    The governance keys (`max_experiments_per_module`, `experiment_sessions*`) are read by the
    advisor package itself, not by the scheduler; they are resolved here too so `run.py status` and
    the config surfaces show one complete block rather than half of one.
    """
    av = cfg.get("advisor", {}) or {}
    return {
        "enabled": av.get("enabled", False),
        # ET, box-local like every other schedule in this file.
        "checkpoints": list(av.get("checkpoints", [
            "09:45", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30",
        ])),
        "deep_at": av.get("deep_at", "17:00"),
        "light_model": av.get("light_model", "sonnet"),
        "deep_model": av.get("deep_model", "opus"),
        "timeout_seconds": int(av.get("timeout_seconds", 600)),
        # One per module by construction: each consumer builds exactly one advised book from the
        # day's artifact, so a second concurrent experiment would have nowhere to be measured.
        "max_experiments_per_module": int(av.get("max_experiments_per_module", 1)),
        # 15 so an experiment that runs its course can satisfy the promotion gate (14 days, 20
        # trades) rather than expiring structurally underpowered.
        "experiment_sessions": int(av.get("experiment_sessions", 15)),
        "experiment_sessions_min": int(av.get("experiment_sessions_min", 5)),
        "experiment_sessions_max": int(av.get("experiment_sessions_max", 30)),
        "modules": {
            "meic": {"enabled": True},
            "flies": {"enabled": False},
            "earnings": {"enabled": True},
            **(av.get("modules") or {}),
        },
    }


def archive_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved end-of-month log/report rotation scheduling. ON by default (opt out with
    `"log_archive": {"enabled": false}`): a monthly task zips each finished month's dated reports and
    rotated log backups into `logs/archive/`. `day` is the day of month it fires; `at` is local time."""
    la = cfg.get("log_archive", {}) or {}
    return {
        "enabled": la.get("enabled", True),
        "task_name": la.get("task_name", "cherrypick-log-archive"),
        "day": int(la.get("day", 1)),
        "at": la.get("at", "03:30"),
    }


def data_epoch(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The active data-epoch marker, or None when unset. An epoch is declared when a
    correctness fix RESTATES what recorded paper history means (e.g. the phase-0
    leg-ratio and win-definition fixes): `{"data_epoch": {"date": "YYYY-MM-DD",
    "note": "..."}}`. `report` surfaces it descriptively (history is never rewritten);
    `calibrate` enforces it — promotion readings use only sessions ON or AFTER the
    epoch date, so a rung can never graduate on numbers produced by retired code."""
    de = cfg.get("data_epoch") or {}
    date = de.get("date")
    if not date:
        return None
    return {"date": str(date), "note": de.get("note")}


def reconcile_schedule_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved scheduled-reconcile config (phase 5: reconcile promoted to a daily task during
    live operation). OFF by default -- the on-demand `cherrypick reconcile` and the serve-only
    dashboard card remain the manual surfaces. When enabled, `install` registers a daily task
    running `reconcile --scheduled`, which notifies on a non-FLAT verdict; its own task, off the
    watchdog tick, so the broker call never rides the reliability path."""
    sch = (cfg.get("reconcile", {}) or {}).get("schedule", {}) or {}
    return {
        "enabled": sch.get("enabled", False),
        "task_name": sch.get("task_name", "cherrypick-reconcile"),
        "at": sch.get("at", "16:30"),
    }


def preopen_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved pre-open supervision config. ON by default — it exists to protect a window that
    cannot be recovered once missed (the 09:30–09:35 opening range), so opting IN would put the
    protection behind a step nobody takes until after it has already cost them a session.

    Its own task on a tight interval rather than a shorter global watchdog interval: dropping the
    full tick to every 2 minutes would multiply module checks, dashboard renders and EOD triggers
    all day to cover 35 minutes. `start`/`end` are ET wall-clock like every other time in this
    config; `install` converts to the host's local time when registering."""
    po = (cfg.get("watchdog", {}) or {}).get("preopen", {}) or {}
    return {
        "enabled": po.get("enabled", True),
        "task_name": po.get("task_name", "cherrypick-preopen"),
        "interval_minutes": po.get("interval_minutes", 2),
        "start": po.get("start", "09:00"),
        "end": po.get("end", "09:35"),
    }


def follow_feed_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved Follow Feed notifier config. OFF by default. When enabled, `install` registers its
    own recurring task -- deliberately NOT a watchdog-tick call like trade_notify, because this is
    the one notifier that makes a network request and the reliability path stays network-free."""
    ff = cfg.get("follow_feed", {}) or {}
    return {
        "enabled": ff.get("enabled", False),
        "task_name": ff.get("task_name", "cherrypick-follow-notify"),
        "interval_minutes": ff.get("interval_minutes", 5),
        "channels": ff.get("channels") or ["log", "discord_follow"],
        "max_per_run": ff.get("max_per_run", 8),
        "filters": ff.get("filters", {}) or {},
    }


def lossdog_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved Lossdog VIP trade-feed notifier config. OFF by default. Network -> its own
    supervisor job, never the watchdog tick (the same treatment as follow_feed, for the same
    reason). Auth is minted per run from the keyring __client cookie, with the LOSSDOG_TOKEN env
    var as the manual fallback -- neither ever appears in this config."""
    ld = cfg.get("lossdog", {}) or {}
    return {
        "enabled": ld.get("enabled", False),
        "task_name": ld.get("task_name", "cherrypick-lossdog-notify"),
        "interval_minutes": ld.get("interval_minutes", 10),
        "channels": ld.get("channels") or ["log", "discord_follow"],
        "max_per_run": ld.get("max_per_run", 8),
        "filters": ld.get("filters", {}) or {},
    }


def desk_notify_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved manual-desk order notifier config. OFF by default. Its own job, never a watchdog-tick
    call: it both pushes to Discord and asks the broker for order status, and the reliability path
    stays free of network calls. It reads the desk's audit journal as a file and never imports
    `cherrypick.desk` -- observing desk orders must not make the submit path reachable from
    scheduled code. `account_number` None means the broker's default account for those credentials."""
    dn = cfg.get("desk_notify", {}) or {}
    return {
        "enabled": dn.get("enabled", False),
        "task_name": dn.get("task_name", "cherrypick-desk-notify"),
        "interval_minutes": dn.get("interval_minutes", 1),
        "channels": dn.get("channels") or ["log", "discord"],
        "journal_path": dn.get("journal_path"),
        "broker_keyring_service": dn.get("broker_keyring_service", "meicagent"),
        "account_number": dn.get("account_number"),
    }


def symbol_watch_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved earnings forward-preview scan config -- packages/earnings' own
    `cherrypick.earnings.symbol_watch`, the source of scout's read-only Earnings page "Upcoming"
    section (expected move, term structure, IV/RV, winrate, historical move stats, and a
    recommended/near-miss/fail tier badge for symbols reporting in the next `days` **trading**
    days, restricted to a liquid-enough universe). OFF by default, and only meaningful once the
    `earnings` module itself is installed (this task shells into that module's own code, same as
    the entry/exit tasks). When enabled, `install` registers a daily task running `symbol_watch
    refresh --days <days>` -- its own task, off the watchdog tick, since a multi-minute
    per-symbol broker-chain scan has no place on the reliability path. `days` should match what
    scout's Upcoming view actually shows (10 trading days by default); widening it here without
    widening scout's own window just spends broker calls on symbols nothing will ever display."""
    sw = cfg.get("symbol_watch", {}) or {}
    return {
        "enabled": sw.get("enabled", False),
        "task_name": sw.get("task_name", "cherrypick-earnings-symbol-watch"),
        "at": sw.get("at", "06:30"),
        "days": int(sw.get("days", 10)),
    }


def console_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolved console config — the suite's one read surface, and the only long-lived HTTP server the
    supervisor keeps up. ON by default (opt out with `"console": {"enabled": false}`).

    Unlike every other job this one is **not** session-scoped: a read surface you can only open
    between 09:30 and 16:00 is useless for reading last night's session, so it declares no window and
    no trading-day gate. `path` resolves like a module's (absolute, or relative to the source root),
    defaulting to the sibling package in this monorepo.

    Liveness is a heartbeat FILE, not an HTTP probe. The server is Node and a wedged event loop stays
    alive while answering nothing, so process-liveness alone would never restart it; the server
    rewrites `state/console.heartbeat` every ~15s and the supervisor's existing silence machinery
    watches its mtime. That also keeps the reliability path free of network calls, per the suite
    invariant. `silence_seconds` is several heartbeats wide so one slow pass never reads as a wedge.

    `dev_backoff_seconds` (default unset) shortens just this job's crash-backoff cap from the
    supervisor's normal 10 minutes -- a dev-only knob for a checkout under active iteration, where a
    real person is restarting it far more often than a crash backoff was ever sized for. Unset means
    the normal cap; leave it that way outside active console development.
    """
    con = cfg.get("console", {}) or {}
    raw = con.get("path") or "../console"
    p = Path(raw)
    root = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
    dev_backoff = con.get("dev_backoff_seconds")
    return {
        "enabled": con.get("enabled", True),
        "root": root,
        "launcher": root / "run.py",
        # The built Node server. Absent means `pnpm build` was never run in that checkout, which is a
        # disabled job with a reason -- never a crash-loop.
        "server_entry": root / "server" / "dist" / "index.js",
        "silence_seconds": int(con.get("silence_seconds", 60)),
        "dev_backoff_seconds": int(dev_backoff) if dev_backoff else None,
    }


def resident_heartbeat_path(name: str) -> Path:
    """Where a supervised resident job publishes its liveness (`state/<name>.heartbeat`).

    The FILENAME convention has exactly one definition, `cherrypick.core.home.heartbeat_path` —
    stated there because the writer (a module, or the console) and the watcher (this package) have to
    agree about it and cannot import each other. Only the directory is re-derived here, off
    `STATE_DIR`, because that is the seam this package's tests redirect (`tests/conftest.py`) and a
    call straight through to the resolver would ignore it.
    """
    return STATE_DIR / _home.heartbeat_path(name).name


def console_heartbeat_path() -> Path:
    """Where the console writes its liveness file (`state/console.heartbeat`). One definition, read by
    the supervisor's silence check and by anything reporting whether the read surface is up."""
    return resident_heartbeat_path("console")


def python_exe() -> str:
    """The interpreter to run module scripts with (same env as cherrypick)."""
    return sys.executable


def pythonw_exe() -> str:
    """A windowless interpreter for scheduled tasks (falls back to python if absent)."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else str(exe)


def ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def state_file(name: str) -> Path:
    ensure_dirs()
    return STATE_DIR / name


def log_file(name: str) -> Path:
    ensure_dirs()
    return LOGS_DIR / name
