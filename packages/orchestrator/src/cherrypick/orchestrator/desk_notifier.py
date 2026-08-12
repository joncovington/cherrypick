"""Discord cards for manual trading-desk orders, and a fill watch over the ones still working.

The desk (`packages/desk`) is a foreground CLI: you submit an order and walk away, and until now
nothing told you what became of it. This notifier closes that loop — a card when an order is
submitted, and a second card when it reaches a terminal state (filled, cancelled, rejected,
expired).

**It never imports `cherrypick.desk`.** The desk's own invariant is that no automated package may
import it, so that the submit path stays unreachable from scheduled code. This reads the desk's
append-only audit journal as a *file* and asks the broker about order ids it finds there. It can
observe desk orders; it cannot create one.

**Network, therefore its own job.** Like `follow_notifier` (and unlike `trade_notifier`, which is
files-only and may ride the watchdog tick), this makes both an HTTP push and a broker call, so it is
a standalone scheduled job and is never invoked from the watchdog. A dead broker or a dead webhook
degrades to "no notifications", never to a failed tick.

**Poll-first, by deliberate design.** The order status the broker reports is authoritative; the
account alert stream is only an accelerator that tells us *when* to look. `wait_for_order_alerts`
failing closed to `[]` means "this call saw nothing", never "no fill happened" — so a fill is never
allowed to depend on the stream. Flies' live loop learned this the same way; the pattern is copied
rather than reinvented.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import util

_STATE = cfgmod.STATE_DIR / "desk_notify.json"
_LOCK = cfgmod.STATE_DIR / "desk_notify.lock"
_LOCK_STALE_SECONDS = 600  # a crashed holder must not wedge desk notification forever
_ID_CAP = 500  # bound the remembered-id lists; the desk is low-volume by nature

# Discord embed colors, matching trade_notifier's vocabulary so the two feeds read as one system.
COLOR_SUBMITTED = 0x3B82F6  # blue — working
COLOR_FILLED = 0x10B981  # green — done
COLOR_CANCELLED = 0x6B7280  # grey — withdrawn, no position change
COLOR_REJECTED = 0xEF4444  # red — the broker refused it
_FIELD_MAX = 1024  # Discord rejects the whole message if any field value exceeds this

# Terminal states, lowercased. Anything not here is still working and stays on the watch list.
_FILLED = "filled"
_TERMINAL_UNFILLED = {"cancelled", "canceled", "rejected", "expired", "removed"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _acquire_lock() -> bool:
    cfgmod.ensure_dirs()
    try:
        if _LOCK.exists() and time.time() - _LOCK.stat().st_mtime > _LOCK_STALE_SECONDS:
            _LOCK.unlink()
    except OSError:
        pass
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    return True


def _release_lock() -> None:
    try:
        _LOCK.unlink()
    except OSError:
        pass


def _save_state(state: dict) -> None:
    cfgmod.ensure_dirs()
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE)


def _cap(ids: list[str]) -> list[str]:
    return ids[-_ID_CAP:]


# --------------------------------------------------------------------------- the desk journal
def journal_path(settings: dict[str, Any]) -> Path:
    """Where the desk writes its audit journal. Config may override; the default mirrors the desk's
    own `journal_path()` without importing the package to ask it."""
    override = settings.get("journal_path")
    if override:
        return Path(str(override)).expanduser()
    return cfgmod.STATE_DIR / "desk" / "journal.jsonl"


def read_submitted(path: Path) -> list[dict[str, Any]]:
    """Every `submitted` event in the desk journal, oldest first. A malformed line is skipped rather
    than aborting the read — the journal is append-only and a torn final line is expected if the
    desk was writing as we read."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("event") == "submitted" and entry.get("order_id"):
                    out.append(entry)
    except OSError:
        return []
    return out


# --------------------------------------------------------------------------- formatting
def _leg_line(leg: dict[str, Any]) -> str:
    action = str(leg.get("action") or "").replace(" to ", " ").title()
    qty = leg.get("quantity")
    right = leg.get("right") or ""
    strike = leg.get("strike")
    exp = leg.get("expiration") or ""
    if strike is not None and right:
        strike_txt = f"{float(strike):g}{right}"
        return f"{action} {qty} {strike_txt} {exp}".strip()
    return f"{action} {qty} {leg.get('symbol') or ''}".strip()


def describe_order(entry: dict[str, Any]) -> str:
    """Human-readable one-block summary of a desk order — the plain-text twin of the card, and the
    canonical form for the log floor and every non-Discord channel."""
    unders = ", ".join(entry.get("underlyings") or []) or "?"
    kind = entry.get("classification") or "order"
    legs = entry.get("legs") or []
    lines = [f"{unders} · {kind} · {len(legs)} leg(s)"]
    lines += [f"  {_leg_line(leg)}" for leg in legs]
    max_loss = entry.get("max_loss")
    if max_loss is None:
        # Never render this as "no risk" — None means uncomputable (unbounded or multi-expiry).
        lines.append("  max loss: uncomputable")
    else:
        lines.append(f"  max loss: ${float(max_loss):,.2f}")
    return "\n".join(lines)


def build_embed(entry: dict[str, Any], *, state: str, status: dict[str, Any] | None = None) -> dict:
    """A Discord card for one desk order.

    One packed "Details" field rather than several inline ones: Discord mobile ignores `inline`, and
    a stack of narrow fields reads as noise on a phone — the same call trade_notifier makes.
    """
    unders = ", ".join(entry.get("underlyings") or []) or "?"
    if state == _FILLED:
        color, verb = COLOR_FILLED, "Filled"
    elif state == "submitted":
        color, verb = COLOR_SUBMITTED, "Submitted"
    elif state in {"rejected", "removed"}:
        color, verb = COLOR_REJECTED, state.title()
    else:
        color, verb = COLOR_CANCELLED, state.title()

    details = describe_order(entry)
    if status:
        price = status.get("price")
        if price is not None:
            label = "fill price" if state == _FILLED else "working price"
            details += f"\n  {label}: {price}"
    embed: dict[str, Any] = {
        "title": f"Desk · {verb} · {unders}"[:256],
        "color": color,
        "fields": [{"name": "Details", "value": details[:_FIELD_MAX]}],
    }
    footer_bits = [f"order {entry.get('order_id')}"]
    if entry.get("account"):
        footer_bits.append(str(entry["account"]))  # already masked by the desk journal
    embed["footer"] = {"text": " · ".join(footer_bits)}
    ts = entry.get("ts")
    if ts:
        embed["timestamp"] = str(ts).replace("+00:00", "Z")
    return embed


# --------------------------------------------------------------------------- broker access
def _order_status(settings: dict[str, Any], order_id: str) -> dict[str, Any] | None:
    """Live status for one order id, or None if the broker could not be asked.

    Account resolution and the status call share **one** event loop. A borrowed session binds its
    async transport to the loop that first drives it, so a second `asyncio.run` would find that
    transport attached to a closed loop — the failure that made `desk propose` impossible until it
    was fixed. Do not split this into two `asyncio.run` calls.
    """
    try:
        import asyncio

        from cherrypick.core import broker as _broker
        from cherrypick.core.auth import SHARED_SERVICE, CredentialStore, SessionManager

        service = str(settings.get("broker_keyring_service") or "meicagent")
        legacy = () if service == SHARED_SERVICE else (SHARED_SERVICE,)
        manager = SessionManager(CredentialStore(service, legacy_service_names=legacy))

        async def _run() -> dict[str, Any]:
            session = manager.get_session()
            account = await _broker.resolve_account(session, settings.get("account_number"))
            return await _broker.order_status(account, session, order_id)

        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 — an unreachable broker means "ask again next run", never a crash
        return None


def classify(status: dict[str, Any] | None) -> str | None:
    """Terminal state name, or None while the order is still working / unknown."""
    if not status:
        return None
    raw = str(status.get("status") or "").strip().lower()
    if raw == _FILLED:
        return _FILLED
    if raw in _TERMINAL_UNFILLED:
        return raw
    return None


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict[str, Any] | None = None, *, status_fn=None) -> dict[str, Any]:
    """One pass: card any newly-submitted desk order, then check the ones still working.

    `status_fn(order_id) -> dict | None` is injectable so tests never touch a broker.
    """
    cfg = cfg if cfg is not None else cfgmod.load_config()
    settings = cfgmod.desk_notify_settings(cfg)
    if not settings["enabled"]:
        return {"ok": True, "skipped": "disabled in config (desk_notify)"}

    if not _acquire_lock():
        return {"ok": True, "skipped": "another desk-notify pass holds the lock"}

    try:
        entries = read_submitted(journal_path(settings))
        state = util.read_json(_STATE) or {}
        announced: list[str] = list(state.get("announced") or [])
        settled: list[str] = list(state.get("settled") or [])
        watching: dict[str, Any] = dict(state.get("watching") or {})

        # Seed, don't backfill: the first run adopts the existing journal silently, otherwise
        # enabling this on a machine with history would fire a card for every order ever placed.
        # Today's orders still go on the watch list, though — an order submitted minutes before
        # this was switched on is exactly the one whose fill you want to hear about, and a
        # yesterday-or-older order reached its terminal state long ago.
        if not state:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            seed_watch = {}
            for entry in entries:
                oid = str(entry["order_id"])
                announced.append(oid)
                if str(entry.get("ts") or "").startswith(today):
                    seed_watch[oid] = entry
            _save_state(
                {
                    "announced": _cap(announced),
                    "settled": [],
                    "watching": seed_watch,
                    "seeded_at": _now_iso(),
                }
            )
            return {"ok": True, "seeded": True, "orders": len(entries), "watching": len(seed_watch)}

        notifier = Notifier({**(cfg.get("notify", {}) or {}), "channels": settings["channels"]})
        pushed = 0

        # --- new submissions
        for entry in entries:
            oid = str(entry["order_id"])
            if oid in announced:
                continue
            notifier.notify(
                "INFO",
                f"desk.submitted.{oid}",
                "Desk order submitted",
                describe_order(entry),
                embed=build_embed(entry, state="submitted"),
            )
            announced.append(oid)
            watching[oid] = entry
            pushed += 1

        # --- fill watch over everything still working
        check = status_fn or (lambda oid: _order_status(settings, oid))
        for oid in list(watching):
            if oid in settled:
                watching.pop(oid, None)
                continue
            status = check(oid)
            terminal = classify(status)
            if terminal is None:
                continue  # still working, or the broker could not be reached — try again next pass
            entry = watching[oid]
            notifier.notify(
                "INFO",
                f"desk.{terminal}.{oid}",
                f"Desk order {terminal}",
                describe_order(entry) + f"\n  status: {terminal}",
                embed=build_embed(entry, state=terminal, status=status),
            )
            settled.append(oid)
            watching.pop(oid, None)
            pushed += 1

        _save_state(
            {
                "announced": _cap(announced),
                "settled": _cap(settled),
                "watching": watching,
                "updated_at": _now_iso(),
            }
        )
        return {"ok": True, "pushed": pushed, "watching": len(watching)}
    finally:
        _release_lock()
