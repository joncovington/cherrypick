"""The position evaluation as a post: the trades needing action, one line each.

A pure function of one `eval-<session>.json` (packages/manage's output), so the same evaluation
always says the same thing. `_run_manage` sends it after every run, scheduled or by hand; the text
is what every channel and the log floor get, and the embed is Discord's card of the same lines.
Advice only, like the evaluation it reads: nothing here names an order to send.
"""

from __future__ import annotations

from typing import Any

# Not acting: a held trade, and one the clock settles without a decision.
QUIET = frozenset({"HOLD", "SETTLES"})

# Most urgent first; an action not named here sorts between CLOSE and REVIEW.
_ORDER = {"CLOSE": 0, "REVIEW": 2}

_RED, _AMBER, _GREEN = 0xE5534B, 0xD4A72C, 0x3FB950

# Discord's embed description limit is 4096; the margin covers the truncation note.
_DESCRIPTION_MAX = 3900


def _money(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/r"
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _line(t: dict[str, Any]) -> str:
    rec = t.get("recommendation") or {}
    action = str(rec.get("action") or "?").replace("_", " ")
    conf = rec.get("confidence")
    what = " ".join(str(x) for x in (t.get("trade_id") or t.get("underlying"), t.get("shape")) if x)
    head = f"**{what}**: {action}" + (f" ({conf})" if conf is not None else "")
    why = rec.get("reason") or rec.get("rule")
    return f"{head} · {_money(t.get('pnl_gross'))} gross" + (f" · {why}" if why else "")


def acting(account: dict[str, Any]) -> list[dict[str, Any]]:
    """The account's trades needing action, most urgent first, then by confidence."""
    out = [
        t
        for t in account.get("trades") or []
        if str((t.get("recommendation") or {}).get("action") or "HOLD") not in QUIET
    ]

    def key(t: dict[str, Any]) -> tuple[int, int]:
        rec = t.get("recommendation") or {}
        return _ORDER.get(str(rec.get("action")), 1), -int(rec.get("confidence") or 0)

    return sorted(out, key=key)


def build(doc: dict[str, Any], at_et: str) -> tuple[str, str, dict[str, Any]]:
    """(title, message, embed) for one evaluation, run at `at_et` (HH:MM ET)."""
    accounts = doc.get("accounts") or []
    many = len(accounts) > 1
    lines: list[str] = []
    n_act = n_all = 0
    worst = None
    for acct in accounts:
        trades = acting(acct)
        n_act += len(trades)
        n_all += len(acct.get("trades") or [])
        if many:
            lines.append(f"__{acct.get('account') or 'account'}__")
        if acct.get("error"):
            lines.append(f"evaluation error: {acct['error']}")
        lines.extend(_line(t) for t in trades)
        for t in trades:
            a = str((t.get("recommendation") or {}).get("action"))
            worst = a if worst is None or _ORDER.get(a, 1) < _ORDER.get(worst, 1) else worst
    if not n_act:
        lines.append(f"Nothing needs action: all {n_all} trades hold.")
    title = f"Positions to manage, {doc.get('session') or '?'} {at_et} ET: {n_act} of {n_all}"
    message = "\n".join(lines) + "\nAdvice only; see the console's Live → positions."
    description = message if len(message) <= _DESCRIPTION_MAX else message[:_DESCRIPTION_MAX] + "\n…"
    color = _GREEN if worst is None else (_AMBER if _ORDER.get(worst, 1) >= 2 else _RED)
    embed = {"title": title, "description": description, "color": color}
    return title, message, embed
