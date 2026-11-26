"""A trade's intent, written where manage reads it: `trades.<key>.intent` in manage.json, and nothing else.

An answer given on the position (the console's card strip and its Intent tab) writes exactly one value. The
key is a stable trade ID (a naked short put, answered `wheel` or `no_wheel`) or a call's long contract (a
call diagonal, answered `pmcc` or `diagonal`), checked by shape here: anything else, a path, a provisional
ID, a put's contract, refuses. A contract that the file already holds under another spelling (spaces are
ignored on read) reuses that spelling, never a second entry.

The text is spliced, so the file's notes, key order and every other entry survive byte for byte; the result
must parse to exactly the intended document, or nothing is written. The write is one `apply_raw_save` (the
backup, the mtime check, the guarded pointers, the atomic write), as every config save. Clearing writes
`null`, which manage reads as no intent. The reply carries the value it replaced, so the page can undo.

Advice only: manage reads the intent on its next evaluation. Nothing here places an order, touches a live
gate or reads the broker, and the module's own rules (what an intent means) stay manage's.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import configedit

TARGET = "manage"
#: A stable trade ID, `<NAME>-<YYMMDD>-<n>` (manage's `tradeids`); a provisional `<NAME>-open-<hash>` is not.
TRADE_ID = re.compile(r"[A-Z][A-Z0-9]{0,9}([./][A-Z0-9]{1,3})?-[0-9]{6}-[0-9]{1,4}")
#: A call's long contract, root then YYMMDD, C, the strike in eight digits; spaces between are the broker's.
CALL_CONTRACT = re.compile(r"[A-Z][A-Z0-9]{0,9}([./][A-Z0-9]{1,3})? *[0-9]{6}C[0-9]{8}")
TRADE_INTENTS = ("wheel", "no_wheel")
CONTRACT_INTENTS = ("pmcc", "diagonal")


def _err(message: str, code: str = "invalid", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code, **extra}


def _escape(key: str) -> str:
    """A key as a JSON-pointer token (RFC 6901)."""
    return key.replace("~", "~0").replace("/", "~1")


def _kind(trade: Any) -> str | None:
    if not isinstance(trade, str):
        return None
    if TRADE_ID.fullmatch(trade):
        return "trade"
    if CALL_CONTRACT.fullmatch(trade):
        return "contract"
    return None


def _existing_key(trades: dict[str, Any], trade: str, kind: str) -> str:
    """The key an entry is already held under: a contract under another spelling, else the one asked for."""
    if kind == "contract":
        # Spaces are ignored, as the PMCC view compares spellings.
        target = "".join(trade.split()).upper()
        for held in trades:
            if isinstance(held, str) and "".join(held.split()).upper() == target:
                return held
    return trade


def save(cfg: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """Set (or clear, with `intent` null) one trade's intent. See the module docstring for every refusal."""
    trade, intent = req.get("trade"), req.get("intent")
    kind = _kind(trade)
    if kind is None:
        return _err("trade: a stable trade ID (XYZ-261001-1) or a call's long contract (XYZ 280121C00040000)")
    allowed = TRADE_INTENTS if kind == "trade" else CONTRACT_INTENTS
    if intent is not None and (not isinstance(intent, str) or intent not in allowed):
        return _err(f"intent: {' or '.join(allowed)}, or null to clear, for {trade}")

    try:
        path = configedit._target_path(cfg, TARGET)
        read_at = path.stat().st_mtime_ns
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
    except (KeyError, OSError, ValueError):
        return _err("manage has no readable config", "not_found")
    trades = doc.get("trades") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or ("trades" in doc and not isinstance(trades, dict)):
        return _err("the manage config's trades block is not an object")

    trades = trades or {}
    key = _existing_key(trades, trade, kind)
    entry = trades.get(key)
    if entry is not None and not isinstance(entry, dict):
        return _err(f"trades.{key} is not an object")
    stored = (entry or {}).get("intent")
    previous = stored if isinstance(stored, str) else None
    if intent == stored:
        return {"ok": True, "changed": False, "trade": key, "intent": intent, "previous": previous}

    want = copy.deepcopy(doc)
    want.setdefault("trades", {}).setdefault(key, {})["intent"] = intent
    try:
        if "trades" not in doc:
            new_text = configedit.splice_or_add(cfg, TARGET, text, "/trades", {key: {"intent": intent}})
        elif entry is None:
            new_text = configedit.insert_key(text, "/trades", key, {"intent": intent})
        elif "intent" in entry:
            new_text = configedit.splice_value(text, f"/trades/{_escape(key)}/intent", intent)
        else:
            new_text = configedit.insert_key(text, f"/trades/{_escape(key)}", "intent", intent)
    except (KeyError, ValueError) as exc:
        return _err(f"cannot place the intent in the config: {exc}")
    # Nothing but the one value may differ: the splice proved it by parsing to exactly the intended document.
    try:
        if json.loads(new_text) != want:
            return _err("the edit would change more than the one intent; nothing was written")
    except ValueError:
        return _err("the edit does not parse; nothing was written")

    expected = req.get("expected_mtime")
    has = isinstance(expected, (int, float)) and not isinstance(expected, bool)
    result = configedit.apply_raw_save(cfg, TARGET, new_text, int(expected) if has else read_at)
    if not result.get("ok"):
        error = str(result.get("error", ""))
        code = "conflict" if "changed on disk" in error else "guarded" if "guarded" in error else "invalid"
        return {**result, "code": code}
    return {**result, "changed": True, "trade": key, "intent": intent, "previous": previous}
