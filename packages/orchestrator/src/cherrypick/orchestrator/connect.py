"""`cherrypick connect` — guided per-module onboarding (credentials + live account).

The Phase-8 onboarding surface: walk a single module through tastytrade setup. Three steps:
  1. OAuth credentials — **delegated to the module's own** `tt.py secrets_set` with the terminal
     inherited (no output capture), so the module's proven hidden-input flow runs and the orchestrator never
     sees, prints, logs, or stores the bearer secrets (client_secret / refresh_token).
  2. Verify the broker connection (read-only `get_connection_status`).
  3. Select the live-trading account (`accounts.list_accounts` → pick → `accounts.set_account`).

Interactive and human-driven. It never places an order, never flips `enable_live_trading`, and never
edits a module's code/config — it only runs the module's own credential tool and writes the destination
account into the module's keyring. Account numbers are masked in everything it prints.
"""

from __future__ import annotations

import subprocess
from typing import Any

from . import accounts, doctor
from . import config as cfgmod
from .util import first_json


def _set_credentials(root) -> bool:
    """Run the module's own hidden-input credential flow for the bearer secrets, tty inherited."""
    print("\n[1/3] tastytrade OAuth credentials (handled by the module; input hidden)")
    proc = subprocess.run(
        [cfgmod.python_exe(), "src/tt.py", "secrets_set", "--keys", "client_secret", "refresh_token"],
        cwd=str(root),
    )
    return proc.returncode == 0


def _verify_connection(root) -> dict[str, Any]:
    print("\n[2/3] Verifying broker connection…")
    status = first_json(doctor._run(root, ["src/tt.py", "get_connection_status"], timeout=35).stdout)
    connected = bool(status.get("ok") or status.get("connected") or status.get("authenticated"))
    count = status.get("account_count")
    detail = "connected" if connected else "NOT connected"
    if count is not None:
        detail += f" ({count} account(s))"
    print(f"      {detail}")
    return {"connected": connected, "account_count": count}


def _select_account(cfg: dict[str, Any], module: str, prompt_fn=input) -> dict[str, Any]:
    print(f"\n[3/3] Select the live-trading account for {module}")
    listing = accounts.list_accounts(cfg, module)
    if not listing.get("ok"):
        print(f"      could not list accounts: {listing.get('error')}")
        return {"ok": False, "error": listing.get("error")}
    rows = listing.get("accounts", [])
    if not rows:
        print("      no accounts returned")
        return {"ok": False, "error": "no accounts"}
    if listing.get("live_enabled") is True:
        print("      NOTE: this module has enable_live_trading=true — the chosen account is where LIVE")
        print("            orders will be placed by the module.")
    for i, a in enumerate(rows, 1):
        mark = "  <- currently designated" if a.get("designated") else ""
        bits = [a["account"]]
        if a.get("nickname"):
            bits.append(str(a["nickname"]))
        if a.get("type"):
            bits.append(str(a["type"]))
        print(f"      {i}) {'  '.join(bits)}{mark}")
    choice = prompt_fn("      Enter a number to designate (or press Enter to leave unset): ").strip()
    if not choice:
        print("      left unchanged.")
        return {"ok": True, "designated": listing.get("designated"), "changed": False}
    result = accounts.set_account(cfg, module, choice)
    if result.get("ok"):
        print(f"      designated {result['designated']} as {module}'s live-trading account.")
    else:
        print(f"      could not set account: {result.get('error')}")
    return {**result, "changed": result.get("ok", False)}


def run(cfg: dict[str, Any], module: str, prompt_fn=input) -> dict[str, Any]:
    """Guided onboarding for one module. Returns a masked summary; prints progress as it goes."""
    mcfg = cfg.get("modules", {}).get(module)
    if not mcfg:
        return {"ok": False, "error": f"unknown module {module!r}"}
    root = cfgmod.module_root(mcfg, module)
    if not root.exists():
        return {"ok": False, "error": f"module checkout not found at {root}"}

    if not _set_credentials(root):
        return {"ok": False, "error": "credential setup did not complete", "step": "secrets_set"}
    conn = _verify_connection(root)
    account = _select_account(cfg, module, prompt_fn=prompt_fn)
    return {
        "ok": True,
        "module": module,
        "connected": conn.get("connected"),
        "account": account.get("designated"),
    }
