"""The module's config: where it is read, the loop's defaults, the arms and the symbols they trade.

The file is `~/.cherrypick/config/ensemble.json` (copied from `config.example.json`, which is what a
checkout without one reads). Three blocks matter:

- `arms`: changes laid over the shipped arms (`arms.load_arms`): `{"iron-fly-1000": {"enabled": false}}`
  switches one off, a whole entry under a new name adds an arm. A misspelt key is refused with its path
  and the loop does not start, so an edit never runs as a different arm. The registry is read by the
  suite's accepted spellings (`arms`, `books`, `profiles`), so none is withdrawn.
- `symbols`: per underlying, the OCC `root` a chain must carry (SPXW keeps the AM-settled SPX monthly
  out), and the `window_strikes` the stream request asks
  for on the entry minutes. A symbol the arms use and this block does not name falls back to
  `DEFAULT_ROOTS` and the symbol itself, so a new arm on a known index needs no code and no config here.
- `defaults`: the loop's knobs (`DEFAULTS`).

Resident loops read config at start (the orchestrator ticks `--once`, so each tick does).
"""

from __future__ import annotations

import pathlib

from cherrypick.core import config as _cfg
from cherrypick.core import home as _home

from . import arms as arms_mod

_PKG_ROOT = str(pathlib.Path(__file__).resolve().parents[3])

#: The loop's knobs. Every one is overridable under `defaults`.
DEFAULTS = {
    #: A quote older than this is not priced against (the suite's usable-quote rule).
    "max_quote_age_seconds": 90,
    #: The spot print a decision or a trail sample is built on must be this fresh.
    "spot_max_age_seconds": 90,
    #: Open positions are marked this often, and on every tick of an exit check.
    "mark_interval_minutes": 15,
    #: The stream request widens for an arm this many minutes before its entry window opens.
    "stream_lead_minutes": 10,
    #: A trail that began later than this many minutes after the open cannot give the opening range or
    #: the five-minute bars: an arm that reads them is refused rather than fed a partial day.
    "trail_grace_minutes": 3,
    #: A close still unposted this many sessions on is logged for `--settle`.
    "settle_alert_sessions": 2,
    #: Daily bars to keep subscribed beyond what an arm's moving average needs.
    "history_margin_days": 15,
}

#: OCC roots by index symbol; any other symbol's root is the symbol itself.
DEFAULT_ROOTS = {"SPX": "SPXW", "NDX": "NDXP", "RUT": "RUTW", "XSP": "XSP"}
DEFAULT_WINDOW_STRIKES = 60


def load_config(path: str | None = None) -> dict:
    return _home.load_module_config("ensemble", _PKG_ROOT, path)


def defaults(config: dict) -> dict:
    return {**DEFAULTS, **_cfg.section(config, "defaults", label="ensemble")}


def arm_overrides(config: dict) -> dict:
    """The `arms` block (or its accepted spellings) as `load_arms` takes it."""
    return dict(_cfg.registry(config, label="ensemble") or {})


def load_arms(config: dict) -> dict[str, dict]:
    """The shipped arms with this machine's changes over them. Raises ValueError, naming every problem."""
    return arms_mod.load_arms(arm_overrides(config))


def enabled(arms: dict[str, dict]) -> dict[str, dict]:
    return {n: a for n, a in arms.items() if a.get("enabled", True)}


def symbols(config: dict, arms: dict[str, dict]) -> dict[str, dict]:
    """{symbol: {root, window_strikes}} for every symbol an enabled arm trades."""
    declared = {
        str(k).upper(): v for k, v in (config.get("symbols") or {}).items() if not str(k).startswith("_")
    }
    out = {}
    for sym in sorted({a["symbol"].upper() for a in enabled(arms).values()}):
        block = declared.get(sym) or {}
        out[sym] = {
            "root": str(block.get("root") or DEFAULT_ROOTS.get(sym, sym)).upper(),
            "window_strikes": int(block.get("window_strikes") or DEFAULT_WINDOW_STRIKES),
        }
    return out
