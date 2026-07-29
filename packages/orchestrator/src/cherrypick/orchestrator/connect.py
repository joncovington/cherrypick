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
from pathlib import Path
from typing import Any

from . import accounts, doctor
from . import config as cfgmod
from .util import first_json


def _set_credentials(root, tool: list[str]) -> bool:
    """Run the module's own hidden-input credential flow for the bearer secrets, tty inherited.
    `tool` is the module's config-declared broker/credential CLI (cfgmod.broker_tool) — tt.py
    for MEIC/earnings, broker_cli.py for flies — so onboarding never assumes a module's layout."""
    print("\n[1/3] tastytrade OAuth credentials (handled by the module; input hidden)")
    proc = subprocess.run(
        [cfgmod.python_exe(), *tool, "secrets_set", "--keys", "client_secret", "refresh_token"],
        cwd=str(root),
    )
    return proc.returncode == 0


def _verify_connection(root, tool: list[str]) -> dict[str, Any]:
    print("\n[2/3] Verifying broker connection…")
    status = first_json(doctor._run(root, [*tool, "get_connection_status"], timeout=35).stdout)
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

    tool = cfgmod.broker_tool(mcfg, module)
    if not _set_credentials(root, tool):
        return {"ok": False, "error": "credential setup did not complete", "step": "secrets_set"}
    conn = _verify_connection(root, tool)
    account = _select_account(cfg, module, prompt_fn=prompt_fn)
    return {
        "ok": True,
        "module": module,
        "connected": conn.get("connected"),
        "account": account.get("designated"),
    }


# --------------------------------------------------------------------------- the suite wizard
def _core_env() -> dict[str, str]:
    """Environment for `python -m cherrypick.core.auth` children: the shared core package on
    PYTHONPATH (resolved from the orchestrator's own import, so it works installed or in-place)."""
    import os

    import cherrypick.core as _core
    core_root = str(Path(_core.__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = core_root + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _shared_setup() -> bool:
    """Hidden-input entry of the SHARED login, in a core child process with the tty inherited —
    the orchestrator never sees the bearer secrets, same fencing as the per-module tools."""
    print("\n[1/5] tastytrade login (stored ONCE, shared by every module; input hidden)")
    proc = subprocess.run(
        [cfgmod.python_exe(), "-m", "cherrypick.core.auth", "setup"], env=_core_env())
    return proc.returncode == 0


def _offer_migration(cfg: dict[str, Any], prompt_fn=input) -> list[dict[str, Any]]:
    """Find per-module secret copies and offer to consolidate them into the shared service
    (deleting the copies — the confirmed decision, so one rotation point remains). Presence is
    read in-process (keyring status only); the VALUES move inside a core child process."""
    from cherrypick.core.auth import REQUIRED_SECRETS, CredentialStore

    with_copies = []
    for name, mcfg in cfgmod.enabled_modules(cfg).items():
        service = cfgmod.module_keyring_service(mcfg, name)
        if not service:
            continue
        try:
            own = CredentialStore(service)
            if any(own.get_secret(k) for k in REQUIRED_SECRETS):
                with_copies.append(service)
        except Exception:
            continue
    if not with_copies:
        return []
    print(f"\n      Found per-module secret copies: {', '.join(with_copies)}.")
    answer = prompt_fn("      Migrate them into the shared login and delete the copies? [y/N]: ")
    if answer.strip().lower() != "y":
        print("      left in place (module copies keep overriding the shared login).")
        return []
    results = []
    for service in with_copies:
        r = subprocess.run(
            [cfgmod.python_exe(), "-m", "cherrypick.core.auth", "migrate",
             "--from-service", service],
            env=_core_env(), capture_output=True, text=True)
        out = first_json(r.stdout) or {"ok": False, "error": "no output"}
        out["service"] = service
        if out.get("skipped_conflicts"):
            print(f"      {service}: CONFLICT on {out['skipped_conflicts']} — shared holds a "
                  "different value; resolve deliberately (see docs/onboarding-redesign.md).")
        elif out.get("ok"):
            print(f"      {service}: migrated {out.get('migrated', [])}")
        else:
            print(f"      {service}: {out.get('error', 'migration failed')}")
        results.append(out)
    return results


def _select_shared_account(cfg: dict[str, Any], prompt_fn=input) -> dict[str, Any]:
    print("\n[3/5] live-trading account (SUITE-WIDE default; per-module designations override)")
    listing = accounts.list_shared(cfg)
    if not listing.get("ok"):
        print(f"      could not list accounts: {listing.get('error')}")
        return {"ok": False, "error": listing.get("error")}
    rows = listing.get("accounts", [])
    for i, a in enumerate(rows, 1):
        mark = "  <- currently designated (shared)" if a.get("designated") else ""
        bits = [a["account"]] + [str(a[k]) for k in ("nickname", "type") if a.get(k)]
        print(f"      {i}) {'  '.join(bits)}{mark}")
        if "ira" in str(a.get("type") or "").lower():
            print("         note: this is an IRA — confirm your options approval level covers "
                  "defined-risk spreads.")
    choice = prompt_fn("      Enter a number to designate (Enter to leave unset): ").strip()
    if not choice:
        print("      left unchanged.")
        return {"ok": True, "designated": listing.get("designated"), "changed": False}
    result = accounts.set_shared_account(cfg, choice)
    if result.get("ok"):
        print(f"      designated {result['designated']} as the suite's live-trading account.")
    else:
        print(f"      could not set account: {result.get('error')}")
    return {**result, "changed": result.get("ok", False)}


def _offer_webhooks(prompt_fn=input) -> None:
    """Opt-in (the confirmed decision): Enter skips. URLs are secrets — read without echo and
    stored straight to the keyring, never printed."""
    import getpass

    from cherrypick.notify import secrets as notify_secrets
    print("\n[4/5] notifications (optional; log + desktop work with no secret. Enter to skip)")
    for channel in notify_secrets.SUPPORTED:
        url = getpass.getpass(f"      {channel} webhook URL (hidden, Enter to skip): ").strip()
        if url:
            notify_secrets.set_webhook(channel, url)
            print(f"      {channel}: stored.")


def _status_panel(cfg: dict[str, Any]) -> None:
    print("\n[5/5] per-module status:")
    ob = accounts.onboarding_status(cfg)
    if not ob.get("ok"):
        print(f"      unavailable: {ob.get('error')}")
        return
    for m in ob["modules"]:
        acct = f"{m['account']} ({m['account_source']})" if m.get("account") else "—"
        print(f"      {m['module']:<10} credentials: {m['credentials']:<8} account: {acct}")


def run_suite(cfg: dict[str, Any], prompt_fn=input) -> dict[str, Any]:
    """The one-command onboarding (`cherrypick connect`, no --module): shared login once,
    optional consolidation of per-module copies, connection check, one suite-wide designation
    (human-confirmed by the selection prompt itself), opt-in webhooks, and the status panel.
    Never touches enable_live_trading / live.enabled / Gate 0 config."""
    if not _shared_setup():
        return {"ok": False, "error": "shared credential setup did not complete"}
    migrations = _offer_migration(cfg, prompt_fn=prompt_fn)

    print("\n[2/5] verifying broker connection…")
    name, mcfg, root, tool = accounts._first_broker_module(cfg)
    conn = {"connected": None}
    if root is not None:
        conn = _verify_connection(root, tool)
    else:
        print("      no enabled module checkout found to verify with")

    account = _select_shared_account(cfg, prompt_fn=prompt_fn)
    _offer_webhooks(prompt_fn=prompt_fn)
    _status_panel(cfg)
    return {
        "ok": True,
        "scope": "suite",
        "connected": conn.get("connected"),
        "account": account.get("designated"),
        "migrations": [{k: v for k, v in m.items() if k != "error"} for m in migrations],
    }
