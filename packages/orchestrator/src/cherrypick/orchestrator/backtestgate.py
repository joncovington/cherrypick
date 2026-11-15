"""The two licence gates the console reads, both FAIL-CLOSED: every other feature fails open (unknown
is visible), these never do, and anything unreadable counts as not holding.

The Backtest page itself is always shown: its register rows, the research queue and studies over our
own data are not the vendor's. Only the pieces that show the vendor's data or results derived from it
are gated, and the two gates answer different questions:

`features.backtest_results` (`results_feature`): may results derived from the vendor's data be shown?
They may while they are on this machine and the licence allows them, which runs past the licence's
end through the keeping and final-review weeks the user chose (#1127) up to the purge date:

1. the operator's own `~/.cherrypick/config/backtest.json` exists and says `enabled: true` (the
   shipped example does not count);
2. today is before the purge date: `vendor.license.terminated_on` absent, or today before it plus
   `purge_within_days` (30 by default, as `licensing.status` counts it);
3. licensed data is here: the vendor store holds a complete session, or the results folder holds a
   file.

`features.backtest` (`backtest_feature`): may new work use the vendor? Only while the licence runs:

1. as above;
2. the vendor licence has not ended (`vendor.license.terminated_on` absent or in the future);
3. the vendor key is in the keyring (read for presence only; the value never leaves this function);
4. the vendor store holds at least one complete session.

The orchestrator drives modules by subprocess and never imports them, so this reads packages/backtest's
files directly: its config, its keyring entry (`<prefix>:<key>` under the configured service, the
core `CredentialStore` layout), its store's `vendor_sessions` table and its results folder. `reasons`
names every condition that failed, for the console's own diagnostics; it never carries a secret.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

DEFAULT_SERVICE = "cherrypick-thetadata"
DEFAULT_KEY = "api_key"
KEY_PREFIX = "production"  # cherrypick.core.auth.credentials' default entry prefix


def _key_present(service: str, key: str) -> bool:
    try:
        import keyring

        return bool(keyring.get_password(service, f"{KEY_PREFIX}:{key}"))
    except Exception:  # noqa: BLE001 -- an unreadable keyring is "not present" here: fail closed
        return False


def _store_has_a_session(vendor_dir: Path) -> bool:
    for db in sorted(vendor_dir.glob("*.db")) if vendor_dir.is_dir() else []:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT 1 FROM vendor_sessions WHERE status = 'complete' LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if row:
            return True
    return False


#: The licence's default window to expunge after its end, as `licensing.status` reads it
#: (`purge_within_days`); a test holds the two equal by reading the backtest package's source.
PURGE_WITHIN_DAYS = 30


def _config(reasons: list[str]) -> dict:
    """The operator's backtest config, appending to `reasons` whatever keeps it from counting."""
    path = _home.config_path("backtest")
    cfg: dict = {}
    if not path.exists():
        reasons.append("no backtest config")
    else:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons.append("backtest config unreadable")
    if not isinstance(cfg, dict):
        cfg = {}
        reasons.append("backtest config is not an object")
    if path.exists() and cfg.get("enabled") is not True:
        reasons.append("backtest disabled")
    return cfg


def _license(cfg: dict) -> dict:
    vendor = cfg.get("vendor") if isinstance(cfg.get("vendor"), dict) else {}
    return vendor.get("license") if isinstance(vendor.get("license"), dict) else {}


def _vendor_dir(cfg: dict) -> Path:
    vendor = cfg.get("vendor") if isinstance(cfg.get("vendor"), dict) else {}
    store = cfg.get("store") if isinstance(cfg.get("store"), dict) else {}
    if store.get("dir"):
        return Path(store["dir"]).expanduser()
    return _home.data_dir("backtest") / "vendor" / str(vendor.get("name") or "thetadata")


def _has_results(results_dir: Path) -> bool:
    try:
        return results_dir.is_dir() and any(p.is_file() for p in results_dir.rglob("*"))
    except OSError:
        return False


def results_feature(today: date | None = None) -> dict[str, Any]:
    """{"available": bool, "reasons": [...]}: whether results derived from the vendor's data may be
    shown. Available only when nothing below failed; the keyring is never read."""
    reasons: list[str] = []
    cfg = _config(reasons)
    lic = _license(cfg)
    ended = lic.get("terminated_on")
    if ended:
        try:
            days = int(lic.get("purge_within_days", PURGE_WITHIN_DAYS))
            purge_by = date.fromisoformat(str(ended)) + timedelta(days=days)
            if (today or date.today()) >= purge_by:
                reasons.append("past the purge date")
        except (TypeError, ValueError):
            reasons.append("vendor licence date unreadable")
    results_dir = _home.data_dir("backtest") / "results"
    if not _store_has_a_session(_vendor_dir(cfg)) and not _has_results(results_dir):
        reasons.append("no licensed data on this machine")
    return {"available": not reasons, "reasons": reasons}


def backtest_feature(today: date | None = None, key_present=_key_present) -> dict[str, Any]:
    """{"available": bool, "reasons": [...]}: whether new work may use the vendor. Available only when
    nothing below failed."""
    reasons: list[str] = []
    cfg = _config(reasons)
    vendor = cfg.get("vendor") if isinstance(cfg.get("vendor"), dict) else {}
    ended = _license(cfg).get("terminated_on")
    if ended:
        try:
            if date.fromisoformat(str(ended)) <= (today or date.today()):
                reasons.append("vendor licence ended")
        except ValueError:
            reasons.append("vendor licence date unreadable")
    cred = vendor.get("credential") if isinstance(vendor.get("credential"), dict) else {}
    # The keyring is asked only once the config says the feature is wanted: nothing reads a secret
    # (even for presence) on a machine that never set this up.
    if reasons:
        reasons.append("vendor key not checked")
    elif not key_present(cred.get("keyring_service") or DEFAULT_SERVICE, cred.get("key") or DEFAULT_KEY):
        reasons.append("no vendor key in the keyring")
    if not _store_has_a_session(_vendor_dir(cfg)):
        reasons.append("no vendor data")
    return {"available": not reasons, "reasons": reasons}
