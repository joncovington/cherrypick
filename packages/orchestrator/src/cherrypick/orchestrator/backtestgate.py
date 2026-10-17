"""Whether the console's backtest page may show: `features.backtest`, FAIL-CLOSED.

Every other feature the console reads fails open (unknown is visible). This one is the declared
exception (2026-10-17): the page shows licensed vendor data and results derived from it, so it is
shown only when every one of these holds, and anything unreadable counts as not holding:

1. the operator's own `~/.cherrypick/config/backtest.json` exists and says `enabled: true` (the
   shipped example does not count);
2. the vendor licence has not ended (`vendor.license.terminated_on` absent or in the future);
3. the vendor key is in the keyring (read for presence only; the value never leaves this function);
4. the vendor store holds at least one complete session.

The orchestrator drives modules by subprocess and never imports them, so this reads packages/backtest's
files directly: its config, its keyring entry (`<prefix>:<key>` under the configured service, the
core `CredentialStore` layout) and its store's `vendor_sessions` table. `reasons` names every
condition that failed, for the console's own diagnostics; it never carries a secret.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
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


def backtest_feature(today: date | None = None, key_present=_key_present) -> dict[str, Any]:
    """{"available": bool, "reasons": [...]}: available only when nothing below failed."""
    reasons: list[str] = []
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
        cfg, reasons = {}, [*reasons, "backtest config is not an object"]
    if path.exists() and cfg.get("enabled") is not True:
        reasons.append("backtest disabled")
    vendor = cfg.get("vendor") if isinstance(cfg.get("vendor"), dict) else {}
    ended = (
        (vendor.get("license") or {}).get("terminated_on")
        if isinstance(vendor.get("license"), dict)
        else None
    )
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
    store = cfg.get("store") if isinstance(cfg.get("store"), dict) else {}
    vendor_dir = (
        Path(store["dir"]).expanduser()
        if store.get("dir")
        else _home.data_dir("backtest") / "vendor" / str(vendor.get("name") or "thetadata")
    )
    if not _store_has_a_session(vendor_dir):
        reasons.append("no vendor data")
    return {"available": not reasons, "reasons": reasons}
