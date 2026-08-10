"""Notify paper-trade entries and exits (e.g. to Discord) as they happen.

Reads each module's paper DB — files only, no broker — finds trades that opened, had a wing stopped, or
closed since the last check, and pushes one concise line per event through the notifier. Distinct from
the watchdog's health
alerts: each trade is a one-shot event tracked by an id watermark (not deduped or re-notified). On first
activation the watermark is seeded to the current DB state, so pre-existing paper trades aren't
backfilled as a burst.

Called both by the dedicated fast trade-notify task (low latency, ~2 min) and as a fallback on each
watchdog tick; the per-schema id watermark keeps either path from re-sending the same event, and the
state file is written atomically so overlapping runs can't corrupt it.

Three paper-DB schemas are wired, dispatched by `paper.trade_schema`:
  - "meic_ic"  : MEICAgent's `ic_trades` table (integer id; entry, per-wing stop, and exit watermarks).
                 A single wing hitting its stop sets status='partial' with a non-null put/call_stop_cost
                 but leaves exit_time NULL, so it is watermarked per (id, wing) — independent of the
                 later whole-IC exit — and fires once the moment that wing's stop cost is recorded.
  - "earnings" : EarningsAgent's `trades` table (text order_id key, opened_at/closed_at timestamps).
  - "fly_book" : cherrypick-flies' `fly_positions` (text position_id key). Three stages rather than
                 two — entry, completion, settlement — because a credit spread turning into a
                 net-credit butterfly is the event the whole module exists to catch.

Trade lines go to the `notify.trade_channels` set (default log + discord) rather than every channel, so
frequent paper fills don't spam desktop toasts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import timeutil, util

_STATE = cfgmod.STATE_DIR / "trade_notify.json"
_ID_CAP = 4000  # bound the remembered-id lists (per schema, per direction)

# The 2-minute trade-notify task and the 10-minute watchdog tick both call run(). The atomic
# _save_state protects against a CORRUPT file, but not against the read-modify-write race: an
# overlap loses one side's watermark update and replays already-notified fills as duplicate
# pushes. A single-writer lockfile closes it — the loser skips, and the next 2-minute tick
# covers anything it would have sent.
_LOCK = cfgmod.STATE_DIR / "trade_notify.lock"
_LOCK_STALE_SECONDS = 600  # a crashed holder must not wedge trade notification forever


def _acquire_lock() -> bool:
    cfgmod.ensure_dirs()
    try:
        if _LOCK.exists() and time.time() - _LOCK.stat().st_mtime > _LOCK_STALE_SECONDS:
            _LOCK.unlink()
    except OSError:
        pass
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
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


def _load_state() -> dict:
    return util.read_json(_STATE)


def _save_state(state: dict) -> None:
    # Atomic replace: the trade-notify task and the watchdog tick can both call run() at once, so a
    # plain truncate-then-write could leave a half-written state file if they overlap.
    cfgmod.ensure_dirs()
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE)


from cherrypick.core.db import connect_ro as _connect_ro  # noqa: E402 — shared read-only opener


# --------------------------------------------------------------------------- MEIC ic_trades schema
def _meic_stopped_wing_keys(conn) -> list:
    """ "{id}:put"/"{id}:call" for every wing that already has a stop cost recorded — the per-wing
    watermark. A wing is stopped exactly when its put/call_stop_cost is non-null (MEIC writes the
    stop's exit price there)."""
    keys = []
    for r in conn.execute(
        "SELECT id, put_stop_cost, call_stop_cost FROM ic_trades "
        "WHERE put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL"
    ):
        if r["put_stop_cost"] is not None:
            keys.append(f"{r['id']}:put")
        if r["call_stop_cost"] is not None:
            keys.append(f"{r['id']}:call")
    return keys


def _meic_seed(conn) -> dict:
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ic_trades").fetchone()[0]
    exited = [r[0] for r in conn.execute("SELECT id FROM ic_trades WHERE exit_time IS NOT NULL")]
    return {
        "last_entry_id": max_id,
        "notified_exit_ids": exited,
        "notified_stop_keys": _meic_stopped_wing_keys(conn),
    }


def _meic_new_entries(conn, last_entry_id: int) -> list:
    return conn.execute(
        "SELECT id, symbol, risk_profile, put_strike, call_strike, wing_width, net_credit, quantity, "
        "entry_time FROM ic_trades WHERE id > ? AND status NOT IN ('pending', 'cancelled', 'partial_entry') "
        "ORDER BY id",
        (last_entry_id,),
    ).fetchall()


def _meic_new_exits(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT id, symbol, risk_profile, exit_reason, pnl, fees, entry_time, exit_time FROM ic_trades "
        "WHERE exit_time IS NOT NULL"
    ).fetchall()
    return [r for r in rows if r["id"] not in notified_ids]


def _meic_new_stops(conn) -> list:
    return conn.execute(
        "SELECT id, symbol, risk_profile, put_strike, call_strike, put_stop_cost, call_stop_cost "
        "FROM ic_trades WHERE put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------- Discord embed cards
# Same card language as follow_notifier's Follow Feed push: a colored stripe carries the lifecycle at
# a glance. Colors are deliberately not a pure green/red pair — a position going on isn't a win and a
# stop isn't the same shape of event as a normal close, so each gets its own color rather than reusing
# "good"/"bad".
#
# One field, not several: Discord's mobile client ignores the `inline` hint entirely and always stacks
# fields one per row regardless of how many would fit, so an embed with four small inline fields (the
# first version of this) rendered as four tall rows in one narrow column instead of a compact card.
# Packing the numbers into a single "Details" line — the same content the plain-text message already
# carries — reads the same on every client instead of gambling on inline layout.
COLOR_ENTRY = 0x3B82F6  # blue — a position went on
COLOR_EXIT = 0xF59E0B  # amber — a position came off
COLOR_STOP = 0xEF4444  # red — one wing stopped out early
COLOR_COMPLETE = 0x10B981  # emerald — flies: the floor just became a guarantee
COLOR_CHOSEN = 0x8B5CF6  # violet — earnings: a symbol cleared the screen (not yet a position)
COLOR_REJECTED = 0x6B7280  # slate — earnings: a symbol was screened and passed over

_FIELD_MAX = 1024  # Discord's per-field value limit; over it the whole message is rejected


def _embed(color: int, title: str, details: str, footer: str | None = None) -> dict:
    embed: dict = {"title": title[:256], "color": color, "fields": [{"name": "Details", "value": details}]}
    if footer:
        embed["footer"] = {"text": footer}
    return embed


def _hhmm_et(ts) -> str:
    """'2026-08-05 09:46:01.123456-04:00' -> '09:46 ET'. MEIC/flies timestamps are `str()` of an
    already-ET-aware datetime (see meic/paper.py, flies/book.py) — sliced rather than parsed, the
    same convention meic's own dashboard table uses. Empty/malformed input drops out cleanly."""
    s = str(ts or "")
    return f"{s[11:16]} ET" if len(s) >= 16 else ""


def _hhmm_epoch(ts) -> str:
    """Earnings timestamps are epoch seconds (opened_at/closed_at), rendered in ET."""
    if ts in (None, ""):
        return ""
    try:
        return timeutil.et_from_epoch(float(ts)).strftime("%H:%M ET")
    except (TypeError, ValueError, OSError):
        return ""


def _embed_meic_entry(r) -> dict:
    entered = _hhmm_et(r["entry_time"])
    details = (
        f"{r['put_strike']:.0f}P/{r['call_strike']:.0f}C w{r['wing_width']:.0f} "
        f"x{r['quantity']} · credit ${r['net_credit']:.2f}"
    )
    if entered:
        details += f" · entered {entered}"
    return _embed(COLOR_ENTRY, f"OPEN · {r['symbol']} iron condor", details, footer=r["risk_profile"])


def _embed_meic_exit(r) -> dict:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    details = f"{r['exit_reason'] or 'closed'} · P&L {pnl_str}"
    entered, exited = _hhmm_et(r["entry_time"]), _hhmm_et(r["exit_time"])
    if entered or exited:
        details += f" · {entered or '?'} → {exited or '?'}"
    return _embed(COLOR_EXIT, f"CLOSE · {r['symbol']} iron condor", details, footer=r["risk_profile"])


def _embed_meic_stop(r, wing: str) -> dict:
    if wing == "put":
        strike, cost, label = r["put_strike"], r["put_stop_cost"], "PUT"
    else:
        strike, cost, label = r["call_strike"], r["call_stop_cost"], "CALL"
    strike_str = f"{strike:.0f}{label[0]}" if strike is not None else label.lower()
    cost_str = f"${cost:.2f}" if cost is not None else "n/a"
    return _embed(
        COLOR_STOP,
        f"STOP · {r['symbol']} {label} wing",
        f"{strike_str} stopped @ {cost_str}",
        footer=r["risk_profile"],
    )


def _fmt_meic_stop(r, wing: str) -> str:
    if wing == "put":
        strike, cost, label = r["put_strike"], r["put_stop_cost"], "PUT"
        strike_str = f"{strike:.0f}P" if strike is not None else "put"
    else:
        strike, cost, label = r["call_strike"], r["call_stop_cost"], "CALL"
        strike_str = f"{strike:.0f}C" if strike is not None else "call"
    cost_str = f"${cost:.2f}" if cost is not None else "n/a"
    return (
        f"\U0001f6d1 MEIC paper STOP — {r['symbol']} {label} wing {strike_str} "
        f"stopped @ {cost_str} [{r['risk_profile']}]"
    )


def _fmt_meic_entry(r) -> str:
    return (
        f"\U0001f7e2 MEIC paper ENTRY — {r['symbol']} "
        f"{r['put_strike']:.0f}P/{r['call_strike']:.0f}C w{r['wing_width']:.0f} "
        f"x{r['quantity']} credit ${r['net_credit']:.2f} [{r['risk_profile']}]"
    )


def _fmt_meic_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    return (
        f"\U0001f534 MEIC paper EXIT — {r['symbol']} [{r['risk_profile']}] "
        f"{r['exit_reason'] or 'closed'}, P&L {pnl_str}"
    )


def _is_summary_profile(risk_profile: str | None, prefixes: tuple[str, ...]) -> bool:
    return bool(prefixes) and str(risk_profile or "").startswith(prefixes)


def _short_arm_label(risk_profile: str) -> str:
    """The digest's compact per-entry tag for a study arm.

    The width-study special cases ('width-5' -> 'w5', 'width-adaptive' -> 'adpt') came out when that
    study was retired 2026-08-05; the current arms (`gex-open`/`gex-blocked`) are short enough to
    render as-is. Kept as the seam rather than inlined at the call site, so the next arm family with
    unwieldy names has one obvious place to add its abbreviation."""
    return risk_profile or "?"


def _money(x: float) -> str:
    return f"+${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"


def _meic_day_totals(conn, symbol: str, day: str, prefixes: tuple[str, ...]) -> tuple[int, float]:
    """Count + net (pnl - fees) of every study-profile trade CLOSED today for one symbol — the
    digest's running day total, independent of what this particular flush window caught."""
    where = " OR ".join(["risk_profile LIKE ?"] * len(prefixes))
    params = [symbol, day] + [f"{p}%" for p in prefixes]
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(pnl - fees), 0) FROM ic_trades "
        f"WHERE symbol = ? AND exit_time IS NOT NULL AND substr(exit_time, 1, 10) = ? "
        f"AND ({where})",
        params,
    ).fetchone()
    return int(row[0] or 0), float(row[1] or 0.0)


def _fmt_meic_summary(conn, pending: dict, day: str, hhmm: str, prefixes: tuple[str, ...]) -> str:
    segments = []
    for symbol in sorted(pending):
        bucket = pending[symbol]
        entries = bucket.get("entries", [])
        exits = bucket.get("exits", [])
        entry_part = f"{len(entries)} {'entry' if len(entries) == 1 else 'entries'}"
        labels = " ".join(_short_arm_label(e) for e in entries)
        if labels:
            entry_part += f" ({labels})"
        exit_net = sum(exits)
        exit_part = f"{len(exits)} {'exit' if len(exits) == 1 else 'exits'} net {_money(exit_net)}"
        day_count, day_net = _meic_day_totals(conn, symbol, day, prefixes)
        segments.append(
            f"{symbol}: {entry_part} · {exit_part} · day {day_count} trades net {_money(day_net)}"
        )
    return f"MEIC digest {hhmm} ET — " + " | ".join(segments)


def _meic_process(
    conn,
    st: dict,
    notifier: Notifier,
    name: str,
    summary_prefixes: tuple[str, ...] = (),
    summary_interval_minutes: float = 15,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    pending = st.setdefault("pending_summary", {})  # symbol -> {"entries": [profile,...], "exits": [net,...]}

    entries = _meic_new_entries(conn, st["last_entry_id"])
    for r in entries:
        if _is_summary_profile(r["risk_profile"], summary_prefixes):
            pending.setdefault(r["symbol"], {"entries": [], "exits": []})["entries"].append(r["risk_profile"])
        else:
            notifier.notify(
                "INFO",
                f"trade.{name}.entry.{r['id']}",
                "Paper entry",
                _fmt_meic_entry(r),
                embed=_embed_meic_entry(r),
            )
        st["last_entry_id"] = max(st["last_entry_id"], r["id"])

    # Per-wing stops: a wing hitting its stop sets put/call_stop_cost but not exit_time, so it is a
    # distinct event from the whole-IC exit and gets its own per-(id, wing) watermark. State that
    # predates this feature carries no stop watermark — seed it to the current stops (like first
    # activation, don't backfill) so pre-existing partials aren't blasted out in one burst.
    # Study-profile stops are folded silently into the digest's eventual exit line (the exit already
    # carries the trade's final P&L) rather than getting their own mid-trade notify.
    stops_notified = 0
    if "notified_stop_keys" not in st:
        st["notified_stop_keys"] = _meic_stopped_wing_keys(conn)
    else:
        stopped = set(st["notified_stop_keys"])
        for r in _meic_new_stops(conn):
            for wing, cost in (("put", r["put_stop_cost"]), ("call", r["call_stop_cost"])):
                if cost is None:
                    continue
                key = f"{r['id']}:{wing}"
                if key in stopped:
                    continue
                if not _is_summary_profile(r["risk_profile"], summary_prefixes):
                    notifier.notify(
                        "INFO",
                        f"trade.{name}.stop.{key}",
                        "Paper stop",
                        _fmt_meic_stop(r, wing),
                        embed=_embed_meic_stop(r, wing),
                    )
                stopped.add(key)
                stops_notified += 1
        st["notified_stop_keys"] = list(stopped)[-_ID_CAP:]

    notified = set(st.get("notified_exit_ids", []))
    exits = _meic_new_exits(conn, notified)
    for r in exits:
        if _is_summary_profile(r["risk_profile"], summary_prefixes):
            net = (r["pnl"] or 0.0) - (r["fees"] or 0.0)
            pending.setdefault(r["symbol"], {"entries": [], "exits": []})["exits"].append(net)
        else:
            notifier.notify(
                "INFO",
                f"trade.{name}.exit.{r['id']}",
                "Paper exit",
                _fmt_meic_exit(r),
                embed=_embed_meic_exit(r),
            )
        notified.add(r["id"])
    st["notified_exit_ids"] = sorted(notified)[-_ID_CAP:]

    summary_pushed = False
    last_flush = st.get("last_summary_flush")
    if last_flush is None:
        st["last_summary_flush"] = now  # first activation of the digest path — flush from here on
    elif pending and (now - last_flush) >= summary_interval_minutes * 60:
        et = timeutil.et_from_epoch(now)
        text = _fmt_meic_summary(
            conn, pending, et.strftime("%Y-%m-%d"), et.strftime("%H:%M"), summary_prefixes
        )
        notifier.notify("INFO", f"trade.{name}.summary.{int(now)}", "Width study digest", text)
        st["pending_summary"] = {}
        st["last_summary_flush"] = now
        summary_pushed = True

    return {
        "entries_notified": len(entries),
        "stops_notified": stops_notified,
        "exits_notified": len(exits),
        "summary_pushed": summary_pushed,
    }


# --------------------------------------------------------------------------- Earnings trades schema
# EarningsAgent's `trades` table keys on a text `order_id` (no integer id) and timestamps opens/closes
# with `opened_at`/`closed_at` (epoch seconds). We watermark by remembering notified order_ids per
# direction — earnings paper volume is a handful of trades a day, so the capped id lists never grow big.
def _all_review_ids(conn) -> list:
    """Every entry_reviews id (guarded — the table is absent on DBs predating the feature)."""
    try:
        return [r["id"] for r in conn.execute("SELECT id FROM entry_reviews").fetchall()]
    except sqlite3.Error:
        return []


def _earnings_seed(conn) -> dict:
    rows = conn.execute("SELECT order_id, closed_at FROM trades").fetchall()
    return {
        "notified_entry_ids": [r["order_id"] for r in rows],
        "notified_exit_ids": [r["order_id"] for r in rows if r["closed_at"] is not None],
        "notified_review_ids": _all_review_ids(conn),
    }


def _earnings_new_entries(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT order_id, strategy, symbol, short_strike, long_call_strike, long_put_strike, "
        "entry_credit, quantity, profile, opened_at FROM trades ORDER BY opened_at"
    ).fetchall()
    return [r for r in rows if r["order_id"] not in notified_ids]


def _earnings_new_exits(conn, notified_ids: set) -> list:
    rows = conn.execute(
        "SELECT order_id, strategy, symbol, pnl, profile, opened_at, closed_at FROM trades "
        "WHERE closed_at IS NOT NULL ORDER BY closed_at"
    ).fetchall()
    return [r for r in rows if r["order_id"] not in notified_ids]


def _earnings_strikes(r) -> str:
    parts = []
    if r["short_strike"] is not None:
        parts.append(f"{r['short_strike']:.0f}S")
    if r["long_put_strike"] is not None:
        parts.append(f"{r['long_put_strike']:.0f}P")
    if r["long_call_strike"] is not None:
        parts.append(f"{r['long_call_strike']:.0f}C")
    return "/".join(parts)


def _fmt_earnings_entry(r) -> str:
    strat = (r["strategy"] or "spread").replace("_", " ")
    strikes = _earnings_strikes(r)
    credit = r["entry_credit"]
    credit_str = f"${credit:.2f}" if credit is not None else "n/a"
    strike_str = f" {strikes}" if strikes else ""
    return (
        f"\U0001f7e2 Earnings paper ENTRY — {r['symbol']} {strat}{strike_str} "
        f"x{r['quantity'] or 1} credit {credit_str} [{r['profile']}]"
    )


def _fmt_earnings_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    strat = (r["strategy"] or "spread").replace("_", " ")
    return f"\U0001f534 Earnings paper EXIT — {r['symbol']} {strat} [{r['profile']}], P&L {pnl_str}"


def _embed_earnings_entry(r) -> dict:
    strat = (r["strategy"] or "spread").replace("_", " ")
    credit = r["entry_credit"]
    credit_str = f"${credit:.2f}" if credit is not None else "n/a"
    strikes = _earnings_strikes(r)
    strike_str = f"{strikes} " if strikes else ""
    details = f"{strike_str}x{r['quantity'] or 1} · credit {credit_str}"
    entered = _hhmm_epoch(r["opened_at"])
    if entered:
        details += f" · entered {entered}"
    return _embed(COLOR_ENTRY, f"OPEN · {r['symbol']} {strat}", details, footer=r["profile"])


def _embed_earnings_exit(r) -> dict:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    strat = (r["strategy"] or "spread").replace("_", " ")
    details = f"P&L {pnl_str}"
    entered, exited = _hhmm_epoch(r["opened_at"]), _hhmm_epoch(r["closed_at"])
    if entered or exited:
        details += f" · {entered or '?'} → {exited or '?'}"
    return _embed(COLOR_EXIT, f"CLOSE · {r['symbol']} {strat}", details, footer=r["profile"])


def _earnings_new_reviews(conn, notified_ids: set) -> list:
    """New per-symbol entry reviews (id watermark). Guarded — the table is absent on older DBs."""
    try:
        rows = conn.execute(
            "SELECT id, scan_date, symbol, timing, price, volume, winrate, winrate_sample, "
            "iv_rv_ratio, term_structure, market_cap, expected_move, best_tier, selected, reason, "
            "profile FROM entry_reviews ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        return []
    return [r for r in rows if r["id"] not in notified_ids]


def _earnings_review_bullets(r) -> list[str]:
    """The screened figures, one bullet each, in the layout the account owner asked for. Shared by the
    plain line and the card so the two can never drift into disagreeing about the same review."""
    lines = []
    if r["price"] is not None:
        lines.append(f"• Price: ${r['price']:,.2f}")
    if r["volume"] is not None:
        lines.append(f"• Volume: {int(r['volume']):,}")
    if r["winrate"] is not None:
        wr = f"{r['winrate'] * 100:.1f}%"
        if r["winrate_sample"] is not None:
            wr += f" over last {int(r['winrate_sample'])} earnings"
        lines.append(f"• Winrate: {wr}")
    if r["iv_rv_ratio"] is not None:
        lines.append(f"• IV/RV Ratio: {r['iv_rv_ratio']:.2f}")
    if r["term_structure"] is not None:
        lines.append(f"• Term Structure: {r['term_structure']:.3f}")
    if r["market_cap"] is not None:
        lines.append(f"• Market Cap: {int(r['market_cap']):,}")
    if r["expected_move"] is not None:
        lines.append(f"• Expected Move: ${r['expected_move']:,.2f}")
    if r["best_tier"]:
        lines.append(f"• Screen: {r['best_tier']}")
    return lines


def _fmt_earnings_review(r) -> str:
    """Per-symbol review summary — the data reviewed for entry plus the chosen/rejected decision, in the
    bullet layout the account owner asked for."""
    icon = "\U0001f7e2" if r["selected"] else "⚪"  # green vs white circle
    decision = "chosen" if r["selected"] else "rejected"
    timing = f" ({r['timing']})" if r["timing"] else ""
    head = f"{icon} Earnings review — {r['symbol']}{timing}: {decision} — {r['reason']} [{r['profile']}]"
    return "\n".join([head, *_earnings_review_bullets(r)])


def _embed_earnings_review(r) -> dict:
    """The review as a card, in the same language as every other trade push: verb · subject in the
    title, the numbers in one Details field, the profile in the footer.

    A review is a screening decision rather than a lifecycle event, so it takes its own two colors
    instead of borrowing entry-blue — a chosen symbol is not yet a position, and reusing the entry
    stripe would make the review and the fill that follows it look like the same event twice."""
    timing = f" ({r['timing']})" if r["timing"] else ""
    verb, color = ("CHOSEN", COLOR_CHOSEN) if r["selected"] else ("REJECTED", COLOR_REJECTED)
    details = "\n".join([str(r["reason"] or ""), *_earnings_review_bullets(r)]).strip()
    # Every other card's Details is bounded numerics; this one leads with a free-text `reason`. Discord
    # rejects the whole message when a field value passes 1024 chars, and the notifier swallows push
    # failures by design — so an over-long reason would drop the notification silently rather than
    # loudly. Truncate here instead.
    if len(details) > _FIELD_MAX:
        details = details[: _FIELD_MAX - 1] + "…"
    return _embed(
        color,
        f"{verb} · {r['symbol']}{timing}",
        details or "no figures recorded",
        footer=r["profile"],
    )


def _earnings_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    entered = set(st.get("notified_entry_ids", []))
    entries = _earnings_new_entries(conn, entered)
    for r in entries:
        notifier.notify(
            "INFO",
            f"trade.{name}.entry.{r['order_id']}",
            "Paper entry",
            _fmt_earnings_entry(r),
            embed=_embed_earnings_entry(r),
        )
        entered.add(r["order_id"])
    st["notified_entry_ids"] = list(entered)[-_ID_CAP:]

    notified = set(st.get("notified_exit_ids", []))
    exits = _earnings_new_exits(conn, notified)
    for r in exits:
        notifier.notify(
            "INFO",
            f"trade.{name}.exit.{r['order_id']}",
            "Paper exit",
            _fmt_earnings_exit(r),
            embed=_embed_earnings_exit(r),
        )
        notified.add(r["order_id"])
    st["notified_exit_ids"] = list(notified)[-_ID_CAP:]

    # Per-symbol entry reviews: the data reviewed for each symbol during the entry scan + the
    # chosen/rejected decision. One push per symbol, id-watermarked like entries/exits.
    reviewed = set(st.get("notified_review_ids", []))
    reviews = _earnings_new_reviews(conn, reviewed)
    for r in reviews:
        notifier.notify(
            "INFO",
            f"trade.{name}.review.{r['id']}",
            "Earnings review",
            _fmt_earnings_review(r),
            embed=_embed_earnings_review(r),
        )
        reviewed.add(r["id"])
    st["notified_review_ids"] = list(reviewed)[-_ID_CAP:]

    return {"entries_notified": len(entries), "exits_notified": len(exits), "reviews_notified": len(reviews)}


# --------------------------------------------------------------------------- flies fly_positions schema
# cherrypick-flies keys on a text `position_id`. A position has THREE notifiable moments, not two: it
# opens as a credit spread, may later be completed into a butterfly, and finally settles. The middle
# one is the whole point of the strategy — it is the moment the position's floor becomes a guarantee —
# so it gets its own watermark rather than being folded into the entry or the exit.
def _flies_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, kind, status FROM fly_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_completion_ids": [r["position_id"] for r in rows if r["kind"] == "fly"],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "settled"],
    }


def _fmt_flies_entry(r) -> str:
    # Four entry modes, not two: `outright` and `bwb_roll` buy the whole structure in one order
    # (nothing left to complete); `legged` and `debit_first` are mirror-image two-stage entries —
    # one sells a credit spread and waits for spot to move toward the far side, the other buys a
    # debit spread and waits to SELL the credit side. Collapsing all non-`outright` modes into
    # "short {side} spread ... credit $X" (the previous code) mislabeled `bwb_roll` as an
    # incomplete short spread when it is already the finished butterfly, and got both the sign and
    # the direction wrong for `debit_first` (a long spread paid for with a debit, not a credit).
    mode = r["entry_mode"]
    if mode == "outright":
        return (
            f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} fly {r['center']:.0f} w{r['wing_width']:.0f} "
            f"bought for ${abs(r['net']):.2f} debit [{r['arm']}]"
        )
    if mode == "bwb_roll":
        far = r["far_width"]
        far_str = f"/{far:.0f}" if far is not None else ""
        return (
            f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} broken-wing fly {r['center']:.0f} "
            f"w{r['wing_width']:.0f}{far_str} bought for ${r['net']:.2f} credit [{r['arm']}]"
        )
    if mode == "debit_first":
        return (
            f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} long {r['side']} spread {r['center']:.0f} "
            f"w{r['wing_width']:.0f} bought for ${abs(r['net']):.2f} debit [{r['arm']}] — needs spot "
            f"{r['completing_direction'] or '?'} to complete (sell the credit side)"
        )
    return (  # legged — the default two-stage entry
        f"\U0001f7e2 Flies paper ENTRY — {r['symbol']} short {r['side']} spread {r['center']:.0f} "
        f"w{r['wing_width']:.0f} credit ${r['net']:.2f} [{r['arm']}] — needs spot "
        f"{r['completing_direction'] or '?'} to complete"
    )


def _fmt_flies_completion(r) -> str:
    """The moment worth waking up for: the position became a butterfly held for a net credit, so
    its worst case at expiry is now a profit. The floor is stated after fees or it means nothing.

    Two different roads get here. `legged`/`debit_first` complete by trading the OTHER side (buying
    or selling a fresh spread against the one already held). `bwb_roll` completes differently — it
    already holds the whole broken-wing butterfly, and completes by rolling its wide far wing IN to
    match the near wing's width (book.py's "roll any open bwb": buy the near-width wing, sell the
    held far wing), which is why `wing_width` here is the near width, not the original far_width."""
    if r["entry_mode"] == "bwb_roll":
        roll_debit = r["roll_debit"]
        roll_str = f" for ${roll_debit:.2f} debit" if roll_debit is not None else ""
        return (
            f"\U0001f98b Flies COMPLETED — {r['symbol']} {r['side']} fly {r['center']:.0f} "
            f"w{r['wing_width']:.0f} — rolled the wide wing in{roll_str}, now ${r['net']:.2f} "
            f"net credit, floor ${r['floor_dollars']:.2f} after fees [{r['arm']}]"
        )
    return (
        f"\U0001f98b Flies COMPLETED — {r['symbol']} {r['side']} fly {r['center']:.0f} "
        f"w{r['wing_width']:.0f} for ${r['net']:.2f} net credit, floor "
        f"${r['floor_dollars']:.2f} after fees [{r['arm']}]"
    )


def _flies_settled_what(r) -> str:
    """What settled, by `kind` (not `entry_mode` — a bwb or a legged spread can each settle either
    completed or not). `fly`/`bwb` never had a spread to name; `long_vertical`/`short_vertical` are
    a `debit_first`/`legged` position that settled before ever completing."""
    return {
        "fly": "fly",
        "bwb": "broken-wing fly",
        "long_vertical": f"long {r['side']} spread",
        "short_vertical": f"short {r['side']} spread",
    }.get(r["kind"], f"short {r['side']} spread")


def _fmt_flies_exit(r) -> str:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    pinned = " (pinned)" if r["pinned"] else ""
    return (
        f"\U0001f534 Flies paper SETTLED — {r['symbol']} {_flies_settled_what(r)} {r['center']:.0f}"
        f"{pinned}, P&L {pnl_str} [{r['arm']}]"
    )


def _embed_flies_entry(r) -> dict:
    # See _fmt_flies_entry for why all four entry modes need distinct handling — `bwb_roll` and
    # `outright` are already the finished structure at entry; `legged` and `debit_first` are the
    # two mirror-image two-stage entries.
    entered = _hhmm_et(r["entry_time"])
    entered_suffix = f" · entered {entered}" if entered else ""
    mode = r["entry_mode"]
    if mode == "outright":
        return _embed(
            COLOR_ENTRY,
            f"OPEN · {r['symbol']} fly",
            f"{r['center']:.0f} w{r['wing_width']:.0f} · debit ${abs(r['net']):.2f}{entered_suffix}",
            footer=r["arm"],
        )
    if mode == "bwb_roll":
        far = r["far_width"]
        far_str = f"/{far:.0f}" if far is not None else ""
        return _embed(
            COLOR_ENTRY,
            f"OPEN · {r['symbol']} broken-wing fly",
            f"{r['center']:.0f} w{r['wing_width']:.0f}{far_str} · credit ${r['net']:.2f}{entered_suffix}",
            footer=r["arm"],
        )
    if mode == "debit_first":
        return _embed(
            COLOR_ENTRY,
            f"OPEN · {r['symbol']} long {r['side']} spread",
            f"{r['center']:.0f} w{r['wing_width']:.0f} · debit ${abs(r['net']):.2f} · "
            f"completes {r['completing_direction'] or '?'} (sells credit side){entered_suffix}",
            footer=r["arm"],
        )
    return _embed(  # legged — the default two-stage entry
        COLOR_ENTRY,
        f"OPEN · {r['symbol']} short {r['side']} spread",
        f"{r['center']:.0f} w{r['wing_width']:.0f} · credit ${r['net']:.2f} · "
        f"completes {r['completing_direction'] or '?'}{entered_suffix}",
        footer=r["arm"],
    )


def _embed_flies_completion(r) -> dict:
    if r["entry_mode"] == "bwb_roll":
        roll_debit = r["roll_debit"]
        roll_str = (
            f"rolled wide wing in for ${roll_debit:.2f} debit"
            if roll_debit is not None
            else "rolled wide wing in"
        )
        details = (
            f"{r['center']:.0f} w{r['wing_width']:.0f} · {roll_str} · net credit ${r['net']:.2f} · "
            f"floor ${r['floor_dollars']:.2f} after fees"
        )
    else:
        details = (
            f"{r['center']:.0f} w{r['wing_width']:.0f} · net credit ${r['net']:.2f} · "
            f"floor ${r['floor_dollars']:.2f} after fees"
        )
    entered, completed = _hhmm_et(r["entry_time"]), _hhmm_et(r["completed_at"])
    if entered or completed:
        details += f" · {entered or '?'} → {completed or '?'}"
    return _embed(COLOR_COMPLETE, f"COMPLETED · {r['symbol']} {r['side']} fly", details, footer=r["arm"])


def _embed_flies_exit(r) -> dict:
    pnl = r["pnl"]
    pnl_str = f"${pnl:+.2f}" if pnl is not None else "n/a"
    pinned = " (pinned)" if r["pinned"] else ""
    details = f"{r['center']:.0f}{pinned} · P&L {pnl_str}"
    entered, exited = _hhmm_et(r["entry_time"]), _hhmm_et(r["exit_time"])
    if entered or exited:
        details += f" · {entered or '?'} → {exited or '?'}"
    title = f"SETTLED · {r['symbol']} {_flies_settled_what(r)}"[:256]
    return _embed(COLOR_EXIT, title, details, footer=r["arm"])


def _flies_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    counts = {}
    stages = [
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_flies_entry,
            _embed_flies_entry,
            "SELECT * FROM fly_positions",
        ),
        (
            "notified_completion_ids",
            "completion",
            "Fly completed",
            _fmt_flies_completion,
            _embed_flies_completion,
            "SELECT * FROM fly_positions WHERE kind = 'fly' AND completed_at IS NOT NULL",
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper settled",
            _fmt_flies_exit,
            _embed_flies_exit,
            "SELECT * FROM fly_positions WHERE status = 'settled'",
        ),
    ]
    for key, event, title, fmt, embed_fn, query in stages:
        notified = set(st.get(key, []))
        rows = [r for r in conn.execute(query).fetchall() if r["position_id"] not in notified]
        for r in rows:
            notifier.notify(
                "INFO", f"trade.{name}.{event}.{r['position_id']}", title, fmt(r), embed=embed_fn(r)
            )
            notified.add(r["position_id"])
        st[key] = sorted(notified)[-_ID_CAP:]
        counts[f"{event}s_notified"] = len(rows)
    return counts


# Registry: paper.trade_schema -> (seed_fn, process_fn). Schemas not listed here skip cleanly.
_SCHEMAS = {
    "meic_ic": (_meic_seed, _meic_process),
    "earnings": (_earnings_seed, _earnings_process),
    "fly_book": (_flies_seed, _flies_process),
}


class _LiveNotifier:
    """Wraps a Notifier so every live-ledger event is unmistakably LIVE: title prefixed, the
    schema formatters' "paper" wording rewritten, and the message pushed with a real-money
    marker. Real money warrants the desktop toast too — paper deliberately doesn't get one.

    Every schema's stage title is hardcoded "Paper entry"/"Paper exit"/etc. (it's the same
    formatter table for both ledgers) — drop that word here rather than double it under the
    "LIVE:" prefix this wrapper already adds. Found 2026-07-30 on flies' first live fill: the
    message body's " paper " -> " LIVE " rewrite below was never mirrored onto the title, so a
    real fill announced itself as "LIVE: Paper entry". Same gap would hit MEIC/Earnings the
    moment either goes live — this wrapper is shared across all three schemas."""

    #: Every LIVE embed gets this color regardless of stage — real money warrants a look distinct
    #: from any paper-ledger card, entry/exit/stop alike.
    COLOR_LIVE = 0xDC2626

    def __init__(self, inner: Notifier):
        self._inner = inner

    def notify(self, level: str, key: str, title: str, message: str, embed: dict | None = None):
        title = title.replace("Paper ", "")
        message = message.replace(" paper ", " LIVE ")
        live_embed = None
        if embed is not None:
            live_embed = {**embed, "title": f"LIVE — {embed['title']}"[:256], "color": self.COLOR_LIVE}
        return self._inner.notify(
            level, f"live.{key}", f"LIVE: {title}", f"\U0001f6a8 {message}", embed=live_embed
        )


# --------------------------------------------------------------------------- entrypoint
def run(cfg: dict | None = None) -> dict:
    if not _acquire_lock():
        # Another invocation (the 2-min task vs the watchdog tick) is mid-run; racing it
        # would replay its already-notified ids. Skip — the next tick covers us.
        return {"ok": True, "skipped": "another trade-notify run holds the lock"}
    try:
        cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
        notify_cfg = cfg.get("notify", {})
        channels = notify_cfg.get("trade_channels", ["log", "discord"])
        notifier = Notifier({**notify_cfg, "channels": channels})
        summary_cfg = notify_cfg.get("trade_summary", {})
        # mode "summary" routes EVERY trade to the digest regardless of profile; the empty prefix
        # matches every risk_profile (str.startswith("") is always True, and the day-totals query's
        # LIKE '%' matches every row). "per-trade" (the default) keeps the prefix routing.
        if summary_cfg.get("mode", "per-trade") == "summary":
            summary_prefixes: tuple[str, ...] = ("",)
        else:
            summary_prefixes = tuple(summary_cfg.get("profile_prefixes", []))
        summary_interval_minutes = summary_cfg.get("interval_minutes", 15)

        state = _load_state()
        summary: dict[str, Any] = {}

        for name, mcfg in cfgmod.enabled_modules(cfg).items():
            paper = mcfg.get("paper", {})
            if not paper.get("notify_trades"):
                continue
            db_path = cfgmod.paper_db_path(mcfg, name)
            if not db_path.exists():
                continue
            schema = paper.get("trade_schema", "meic_ic")
            adapter = _SCHEMAS.get(schema)
            if adapter is None:  # unknown schema — skip cleanly
                continue
            seed_fn, process_fn = adapter

            conn = _connect_ro(db_path)
            try:
                st = state.get(name)
                if st is None:  # first activation — seed, don't backfill
                    state[name] = seed_fn(conn)
                    summary[name] = {"seeded": True}
                    continue
                if schema == "meic_ic":
                    summary[name] = process_fn(
                        conn,
                        st,
                        notifier,
                        name,
                        summary_prefixes=summary_prefixes,
                        summary_interval_minutes=summary_interval_minutes,
                    )
                else:
                    summary[name] = process_fn(conn, st, notifier, name)
            finally:
                conn.close()

        # LIVE ledgers — same schema adapters over the module's live_db, separately opted in via
        # live.notify_trades, with per-module state under "<name>:live" so ids never collide with
        # paper's. All push happens HERE (the 2-min task / watchdog tick), never in the trading
        # loop itself — the no-network-on-the-loop invariant, at a worst-case ~2-min lag.
        for name, mcfg in cfgmod.enabled_modules(cfg).items():
            live = mcfg.get("live") or {}
            if not live.get("notify_trades"):
                continue
            db_path = cfgmod.live_db_path(mcfg, name)
            if db_path is None or not db_path.exists():
                continue
            schema = mcfg.get("paper", {}).get("trade_schema", "meic_ic")
            adapter = _SCHEMAS.get(schema)
            if adapter is None:
                continue
            seed_fn, process_fn = adapter
            live_channels = sorted(set(channels) | {"desktop"})
            live_notifier = _LiveNotifier(Notifier({**notify_cfg, "channels": live_channels}))
            key = f"{name}:live"
            conn = _connect_ro(db_path)
            try:
                st = state.get(key)
                if st is None:  # first activation — seed, don't backfill
                    state[key] = seed_fn(conn)
                    summary[key] = {"seeded": True}
                    continue
                if schema == "meic_ic":
                    summary[key] = process_fn(
                        conn,
                        st,
                        live_notifier,
                        name,
                        summary_prefixes=(),
                        summary_interval_minutes=summary_interval_minutes,
                    )
                else:
                    summary[key] = process_fn(conn, st, live_notifier, name)
            finally:
                conn.close()

        _save_state(state)
        return {"ok": True, "modules": summary}
    finally:
        _release_lock()
