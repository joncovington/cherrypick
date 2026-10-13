"""Notify paper-trade entries and exits (e.g. to Discord) as they happen.

Reads each module's paper DB — files only, no broker — finds trades that opened, had a wing stopped, or
closed since the last check, and pushes one concise line per event through the notifier. Distinct from
the watchdog's health
alerts: each trade is a one-shot event tracked by an id watermark (not deduped or re-notified). On first
activation the watermark is seeded to the current DB state, so pre-existing paper trades aren't
backfilled as a burst -- and so is a module switched back on, or one left unprocessed past
`_STALE_DAYS`, whose history is not news.

Called both by the dedicated fast trade-notify task and as a fallback on each watchdog tick; the
per-schema id watermark keeps either path from re-sending the same event, and the state file is
written atomically so overlapping runs can't corrupt it. The state is kept small and quiet: compact
JSON, written only when it changed, holding only modules still notified; integer ids remembered as
a capped list plus a floor (`_trim_ids`), text ids pruned to the ledger (`_run_stages`), and the
MEIC digest bucket as per-arm counts. Every exit card's "P&L" is net, by `core.ledgers.ROW_NET`.

Three paper-DB schemas are wired, dispatched by `paper.trade_schema`:
  - "meic_ic"  : MEICAgent's `ic_trades` table (integer id; entry, per-wing stop, and exit watermarks).
                 A single wing hitting its stop sets status='partial' with a non-null put/call_stop_cost
                 but leaves exit_time NULL, so it is watermarked per (id, wing) — independent of the
                 later whole-IC exit — and fires once the moment that wing's stop cost is recorded.
  - "earnings" : EarningsAgent's `trades` table (text order_id key, opened_at/closed_at timestamps).
  - "fly_book" : cherrypick-flies' `fly_positions` (text position_id key). Three stages rather than
                 two — entry, completion, settlement — because a credit spread turning into a
                 net-credit butterfly is the event the whole module exists to catch.

Trade lines go to the `notify.trade_channels` set (default log only) rather than every channel, so
frequent paper fills don't spam desktop toasts.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from datetime import time as dtime
from typing import Any

from cherrypick.notify import Notifier

from . import config as cfgmod
from . import schemas, timeutil, util

_STATE = cfgmod.STATE_DIR / "trade_notify.json"
_ID_CAP = 4000  # bound an integer remembered-id list; ids below the cut ride the floor (_trim_ids)

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


#: A module's state not processed for longer than this is re-seeded from its ledger rather than
#: caught up: whatever happened in between is history, not news, and catching up is a flood. MEIC's
#: state sat from 2026-08-19 with its notify switched off while the ledger went from id 6,495 to
#: 23,426; switching it back on would have pushed ~17,000 old exits.
_STALE_DAYS = 3


def _load_state() -> dict:
    return util.read_json(_STATE)


def _stale(st: dict, today) -> bool:
    """Has this module's state gone unprocessed for longer than `_STALE_DAYS`? State written before
    the stamp existed has none and is taken as current: it was processed on the previous pass."""
    stamp = st.get("processed_on")
    if not stamp:
        return False
    try:
        last = datetime.strptime(str(stamp), "%Y-%m-%d").date()
    except ValueError:
        return True
    return (today - last).days > _STALE_DAYS


def _dumps(state: dict) -> str:
    """The state's one serialization: compact and key-sorted, so an unchanged state is the same
    text pass after pass and the comparison in `_save_state` means something."""
    return json.dumps(state, separators=(",", ":"), sort_keys=True)


def _save_state(state: dict, *, unchanged_from: str | None = None) -> bool:
    """Write the state if it changed; returns whether bytes were written.

    Until 2026-10-12 every pass rewrote the whole file, indented, changed or not: 7.3 MB every 30
    seconds around the clock. An unchanged pass now only touches the file's mtime, which is what
    `watchdog._check_trade_notify` reads as "passes are completing"."""
    # Atomic replace: the trade-notify task and the watchdog tick can both call run() at once, so a
    # plain truncate-then-write could leave a half-written state file if they overlap.
    cfgmod.ensure_dirs()
    text = _dumps(state)
    if unchanged_from is not None and text == unchanged_from and _STATE.exists():
        try:
            os.utime(_STATE, None)
            return False
        except OSError:
            pass  # fall through to a full write rather than let the watchdog read a dead notifier
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, _STATE)
    return True


def _trim_ids(ids, floor: int, id_of=int) -> tuple[list, int]:
    """(kept, floor) for an integer-keyed remembered-id list: sorted, capped at `_ID_CAP` by
    dropping the OLDEST ids and raising `floor` past them, so everything at or below the floor
    counts as already notified.

    A plain cap is not enough. The id lists used to be cut to the newest 4,000 and the rest
    forgotten, while the query still returned every row ever closed -- so from the 4,001st exit on,
    the oldest ones looked new again on every pass. MEIC's digest bucket held 271,000 replayed
    exits when it was last flushed."""
    kept = sorted({i for i in ids if id_of(i) > floor}, key=lambda i: (id_of(i), str(i)))
    if len(kept) > _ID_CAP:
        floor = max(floor, id_of(kept[-_ID_CAP - 1]))
        kept = kept[-_ID_CAP:]
    return kept, floor


from cherrypick.core import db as _db  # noqa: E402
from cherrypick.core import ledgers as _ledgers  # noqa: E402 — the one per-schema net rule
from cherrypick.core.db import connect_ro as _connect_ro  # noqa: E402 — shared read-only opener


# --------------------------------------------------------------------------- MEIC ic_trades schema
def _meic_stopped_wing_keys(conn, floor: int = 0) -> list:
    """ "{id}:put"/"{id}:call" for every wing above `floor` that already has a stop cost recorded —
    the per-wing watermark. A wing is stopped exactly when its put/call_stop_cost is non-null (MEIC
    writes the stop's exit price there)."""
    keys = []
    for r in conn.execute(
        "SELECT id, put_stop_cost, call_stop_cost FROM ic_trades "
        "WHERE (put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL) AND id > ?",
        (floor,),
    ):
        if r["put_stop_cost"] is not None:
            keys.append(f"{r['id']}:put")
        if r["call_stop_cost"] is not None:
            keys.append(f"{r['id']}:call")
    return keys


def _stop_key_id(key: str) -> int:
    return int(str(key).split(":", 1)[0])


def _meic_open_floor(conn) -> int:
    """The highest id at or below which no IC can still produce an event: one below the oldest IC
    still open, or the newest id when none is. A cancelled row never exits, so it does not hold
    the floor down."""
    lo = conn.execute(
        "SELECT MIN(id) FROM ic_trades WHERE exit_time IS NULL AND status != 'cancelled'"
    ).fetchone()[0]
    if lo is not None:
        return int(lo) - 1
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM ic_trades").fetchone()[0])


def _meic_seed(conn) -> dict:
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM ic_trades").fetchone()[0]
    floor = _meic_open_floor(conn)
    exited = [
        r[0]
        for r in conn.execute("SELECT id FROM ic_trades WHERE exit_time IS NOT NULL AND id > ?", (floor,))
    ]
    return {
        "last_entry_id": max_id,
        "exit_floor_id": floor,
        "notified_exit_ids": sorted(exited),
        "stop_floor_id": floor,
        "notified_stop_keys": sorted(
            _meic_stopped_wing_keys(conn, floor), key=lambda k: (_stop_key_id(k), k)
        ),
    }


def _legacy_floor(ids: list) -> int:
    """The floor implied by a remembered-id list from before floors existed: it was the newest ids
    kept in order, so everything below its oldest member had already been notified."""
    return min(ids) - 1 if ids else 0


def _meic_new_entries(conn, last_entry_id: int) -> list:
    arm = _db.arm_column(conn, "ic_trades")
    return conn.execute(
        f"SELECT id, symbol, {arm} AS arm, put_strike, call_strike, wing_width, net_credit, quantity, "
        "entry_time FROM ic_trades WHERE id > ? AND status NOT IN ('pending', 'cancelled', 'partial_entry') "
        "ORDER BY id",
        (last_entry_id,),
    ).fetchall()


def _meic_new_exits(conn, notified_ids: set, floor: int = 0) -> list:
    arm = _db.arm_column(conn, "ic_trades")
    rows = conn.execute(
        f"SELECT id, symbol, {arm} AS arm, exit_reason, pnl, fees, entry_time, exit_time FROM ic_trades "
        "WHERE exit_time IS NOT NULL AND id > ? ORDER BY id",
        (floor,),
    ).fetchall()
    return [r for r in rows if r["id"] not in notified_ids]


def _meic_new_stops(conn, floor: int = 0) -> list:
    arm = _db.arm_column(conn, "ic_trades")
    return conn.execute(
        f"SELECT id, symbol, {arm} AS arm, put_strike, call_strike, put_stop_cost, call_stop_cost "
        "FROM ic_trades WHERE (put_stop_cost IS NOT NULL OR call_stop_cost IS NOT NULL) AND id > ? "
        "ORDER BY id",
        (floor,),
    ).fetchall()


def _signed(x) -> str:
    """A signed dollar figure, or "n/r" (not recorded) -- never a zero standing in for a gap."""
    return f"${x:+.2f}" if x is not None else "n/r"


def _net(schema: str, r) -> float | None:
    """An exit row's net P&L by `core.ledgers`' rule for its schema. "P&L" on every card is this
    figure: MEIC's and earnings' `pnl` columns are GROSS, and until 2026-10-12 their cards printed
    them under "P&L", overstating every exit by its costs."""
    return _ledgers.ROW_NET[schema](r)


# --------------------------------------------------------------------------- Discord embed cards
# Same card language as the (since-moved) follow-feed push: a colored stripe carries the lifecycle at
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
COLOR_DIGEST = 0x6366F1  # indigo — the periodic MEIC roll-up: many trades, no single lifecycle event

_FIELD_MAX = 1024  # Discord's per-field value limit; over it the whole message is rejected

#: The digest is an intraday push and nothing else: MEIC only trades the session, so a roll-up that
#: fires at 03:00 on a Saturday carries the same figures the last in-session one already carried.
#: Outside this ET window — and on any non-trading day — a due flush is *held*, not dropped: the
#: pending batch stays in state and goes out on the next tick inside the window. The tail runs past
#: the bell because the day's last exits land at the close and still belong to that day's digest.
_DIGEST_OPEN = dtime(9, 15)
_DIGEST_CLOSE = dtime(17, 0)


def _digest_window_open(now: float) -> bool:
    et = timeutil.et_from_epoch(now)
    if not timeutil.is_trading_day(et, timeutil.load_holidays([et.year])):
        return False
    return _DIGEST_OPEN <= et.time() <= _DIGEST_CLOSE


def _arm(r) -> str:
    """The arm a row belongs to, under any name its ledger has used (`core.db.ARM_COLUMNS`):
    calendars/pmcc/curve/bwb said `book` until 2026-09-23, meic says `risk_profile` until its own
    window. The meic selects alias to `arm` already; this is the net under a row that did not.
    Reading only `book` raised on the first post-rename event, which aborted the whole pass before
    its state was saved -- and every later pass then re-sent every flies event of the day."""
    keys = r.keys()
    for name in _db.ARM_COLUMNS:
        if name in keys:
            return r[name]
    raise KeyError(f"row has no arm column (looked for {', '.join(_db.ARM_COLUMNS)})")


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
    return _embed(COLOR_ENTRY, f"OPEN · {r['symbol']} iron condor", details, footer=_arm(r))


def _embed_meic_exit(r) -> dict:
    details = (
        f"{r['exit_reason'] or 'closed'} · P&L {_signed(_net('meic_ic', r))} "
        f"(gross {_signed(r['pnl'])}, fees ${r['fees'] or 0:.2f})"
    )
    entered, exited = _hhmm_et(r["entry_time"]), _hhmm_et(r["exit_time"])
    if entered or exited:
        details += f" · {entered or '?'} → {exited or '?'}"
    return _embed(COLOR_EXIT, f"CLOSE · {r['symbol']} iron condor", details, footer=_arm(r))


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
        footer=_arm(r),
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
        f"stopped @ {cost_str} [{_arm(r)}]"
    )


def _fmt_meic_entry(r) -> str:
    return (
        f"\U0001f7e2 MEIC paper ENTRY — {r['symbol']} "
        f"{r['put_strike']:.0f}P/{r['call_strike']:.0f}C w{r['wing_width']:.0f} "
        f"x{r['quantity']} credit ${r['net_credit']:.2f} [{_arm(r)}]"
    )


def _fmt_meic_exit(r) -> str:
    return (
        f"\U0001f534 MEIC paper EXIT — {r['symbol']} [{_arm(r)}] "
        f"{r['exit_reason'] or 'closed'}, P&L {_signed(_net('meic_ic', r))} (gross {_signed(r['pnl'])})"
    )


def _is_summary_profile(arm: str | None, prefixes: tuple[str, ...]) -> bool:
    return bool(prefixes) and str(arm or "").startswith(prefixes)


def _short_arm_label(arm: str) -> str:
    """The digest's compact per-entry tag for a study arm.

    The width-study special cases ('width-5' -> 'w5', 'width-adaptive' -> 'adpt') came out when that
    study was retired 2026-08-05; the current arms (`gex-open`/`gex-blocked`) are short enough to
    render as-is. Kept as the seam rather than inlined at the call site, so the next arm family with
    unwieldy names has one obvious place to add its abbreviation."""
    return arm or "?"


def _money(x: float) -> str:
    return f"+${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"


def _meic_day_totals(conn, symbol: str, day: str, prefixes: tuple[str, ...]) -> tuple[int, float]:
    """Count + net (pnl - fees) of every study-profile trade CLOSED today for one symbol — the
    digest's running day total, independent of what this particular flush window caught."""
    arm = _db.arm_column(conn, "ic_trades")
    where = " OR ".join([f"{arm} LIKE ?"] * len(prefixes))
    params = [symbol, day] + [f"{p}%" for p in prefixes]
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(pnl - COALESCE(fees, 0)), 0) FROM ic_trades "
        f"WHERE symbol = ? AND exit_time IS NOT NULL AND substr(exit_time, 1, 10) = ? "
        f"AND ({where})",
        params,
    ).fetchone()
    return int(row[0] or 0), float(row[1] or 0.0)


def _pending_bucket(bucket: dict | None) -> dict:
    """One symbol's pending digest figures as {"entries": {arm: count}, "exits": count, "net": sum}.

    Aggregated rather than listed so the bucket is bounded by the number of arms, not trades: the
    list form ({"entries": [arm, ...], "exits": [net, ...]}, read here for state written before
    2026-10-12) held one element per event and reached 271,000 exits in one bucket."""
    bucket = bucket or {}
    entries = bucket.get("entries") or {}
    if isinstance(entries, list):
        counted: dict[str, int] = {}
        for arm in entries:
            counted[arm] = counted.get(arm, 0) + 1
        entries = counted
    exits = bucket.get("exits") or 0
    net = bucket.get("net") or 0.0
    if isinstance(exits, list):
        exits, net = len(exits), net + sum(exits)
    return {"entries": dict(entries), "exits": int(exits), "net": round(float(net), 2)}


def _meic_symbol_segment(conn, symbol: str, bucket: dict, day: str, prefixes: tuple[str, ...]) -> list[str]:
    """One symbol's digest figures as three parts (entries / exits / day total), shared by the plain
    line and the embed card so the two can never drift. Arms are counted, not listed — a 30-entry
    window would otherwise repeat the same three labels ten times each."""
    bucket = _pending_bucket(bucket)
    n_entries = sum(bucket["entries"].values())
    entry_part = f"{n_entries} {'entry' if n_entries == 1 else 'entries'}"
    counts: dict[str, int] = {}
    for arm, n in bucket["entries"].items():
        label = _short_arm_label(arm)
        counts[label] = counts.get(label, 0) + n
    labels = " ".join(f"{arm}×{n}" if n > 1 else arm for arm, n in sorted(counts.items()))
    if labels:
        entry_part += f" ({labels})"
    n_exits = bucket["exits"]
    exit_part = f"{n_exits} {'exit' if n_exits == 1 else 'exits'} net {_money(bucket['net'])}"
    day_count, day_net = _meic_day_totals(conn, symbol, day, prefixes)
    return [entry_part, exit_part, f"day {day_count} trades net {_money(day_net)}"]


def _fmt_meic_summary(conn, pending: dict, day: str, hhmm: str, prefixes: tuple[str, ...]) -> str:
    segments = [
        f"{symbol}: " + " · ".join(_meic_symbol_segment(conn, symbol, pending[symbol], day, prefixes))
        for symbol in sorted(pending)
    ]
    return f"MEIC digest {hhmm} ET — " + " | ".join(segments)


def _embed_meic_summary(conn, pending: dict, day: str, hhmm: str, prefixes: tuple[str, ...]) -> dict:
    """The digest as a card: one field per symbol (fields stack one per row on every client — see the
    embed color note above), the same figures as the plain line, one per line instead of dot-joined."""
    fields = [
        {
            "name": symbol[:256],
            "value": "\n".join(_meic_symbol_segment(conn, symbol, pending[symbol], day, prefixes))[
                :_FIELD_MAX
            ],
        }
        for symbol in sorted(pending)
    ]
    return {
        "title": f"DIGEST · MEIC {hhmm} ET",
        "color": COLOR_DIGEST,
        "fields": fields,
    }


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
    # symbol -> {"entries": {arm: n}, "exits": n, "net": x}; see _pending_bucket for the old shape.
    pending = {sym: _pending_bucket(b) for sym, b in (st.get("pending_summary") or {}).items()}
    st["pending_summary"] = pending

    # Legacy state (before floors) kept the newest ids in order, so its oldest member marks the floor.
    if "exit_floor_id" not in st:
        st["exit_floor_id"] = _legacy_floor(st.get("notified_exit_ids") or [])
    if "stop_floor_id" not in st:
        st["stop_floor_id"] = st["exit_floor_id"]

    entries = _meic_new_entries(conn, st["last_entry_id"])
    for r in entries:
        if _is_summary_profile(_arm(r), summary_prefixes):
            bucket = pending.setdefault(r["symbol"], _pending_bucket(None))
            bucket["entries"][_arm(r)] = bucket["entries"].get(_arm(r), 0) + 1
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
        st["notified_stop_keys"], st["stop_floor_id"] = _trim_ids(
            _meic_stopped_wing_keys(conn, st["stop_floor_id"]), st["stop_floor_id"], _stop_key_id
        )
    else:
        stopped = set(st["notified_stop_keys"])
        for r in _meic_new_stops(conn, st["stop_floor_id"]):
            for wing, cost in (("put", r["put_stop_cost"]), ("call", r["call_stop_cost"])):
                if cost is None:
                    continue
                key = f"{r['id']}:{wing}"
                if key in stopped:
                    continue
                if not _is_summary_profile(_arm(r), summary_prefixes):
                    notifier.notify(
                        "INFO",
                        f"trade.{name}.stop.{key}",
                        "Paper stop",
                        _fmt_meic_stop(r, wing),
                        embed=_embed_meic_stop(r, wing),
                    )
                stopped.add(key)
                stops_notified += 1
        st["notified_stop_keys"], st["stop_floor_id"] = _trim_ids(stopped, st["stop_floor_id"], _stop_key_id)

    notified = set(st.get("notified_exit_ids", []))
    exits = _meic_new_exits(conn, notified, st["exit_floor_id"])
    for r in exits:
        if _is_summary_profile(_arm(r), summary_prefixes):
            bucket = pending.setdefault(r["symbol"], _pending_bucket(None))
            bucket["exits"] += 1
            bucket["net"] = round(bucket["net"] + (_net("meic_ic", r) or 0.0), 2)
        else:
            notifier.notify(
                "INFO",
                f"trade.{name}.exit.{r['id']}",
                "Paper exit",
                _fmt_meic_exit(r),
                embed=_embed_meic_exit(r),
            )
        notified.add(r["id"])
    st["notified_exit_ids"], st["exit_floor_id"] = _trim_ids(notified, st["exit_floor_id"])

    summary_pushed = False
    last_flush = st.get("last_summary_flush")
    if last_flush is None:
        st["last_summary_flush"] = now  # first activation of the digest path — flush from here on
    elif pending and (now - last_flush) >= summary_interval_minutes * 60 and _digest_window_open(now):
        et = timeutil.et_from_epoch(now)
        day, hhmm = et.strftime("%Y-%m-%d"), et.strftime("%H:%M")
        notifier.notify(
            "INFO",
            f"trade.{name}.summary.{int(now)}",
            "Trade digest",
            _fmt_meic_summary(conn, pending, day, hhmm, summary_prefixes),
            embed=_embed_meic_summary(conn, pending, day, hhmm, summary_prefixes),
        )
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
    reviews = _all_review_ids(conn)
    return {
        "notified_entry_ids": sorted(r["order_id"] for r in rows),
        "notified_exit_ids": sorted(r["order_id"] for r in rows if r["closed_at"] is not None),
        # Reviews are one-shot rows with a rising integer id: the newest one is the whole watermark.
        "review_floor_id": max(reviews, default=0),
        "notified_review_ids": [],
    }


def _earnings_present(conn) -> tuple[set, set] | None:
    """(every order_id, every closed order_id): what a remembered-id list is pruned to, so it is
    bounded by the ledger and never cut short of it (a cut list replays what it forgot). None when
    the table cannot be read, in which case the lists are kept as they are."""
    try:
        rows = conn.execute("SELECT order_id, closed_at FROM trades").fetchall()
    except sqlite3.Error:
        return None
    return {r["order_id"] for r in rows}, {r["order_id"] for r in rows if r["closed_at"] is not None}


def _pruned(ids: set, present: set | None) -> list:
    """A text-keyed remembered-id list, sorted and pruned to the ids the ledger still holds."""
    return sorted(ids if present is None else ids & present)


def _earnings_new_entries(conn, notified_ids: set) -> list:
    arm = _db.arm_column(conn, "trades")  # `profile` until earnings' column moves
    rows = conn.execute(
        "SELECT order_id, strategy, symbol, short_strike, long_call_strike, long_put_strike, "
        f"entry_credit, quantity, {arm} AS arm, opened_at FROM trades ORDER BY opened_at"
    ).fetchall()
    return [r for r in rows if r["order_id"] not in notified_ids]


def _earnings_new_exits(conn, notified_ids: set) -> list:
    arm = _db.arm_column(conn, "trades")
    rows = conn.execute(
        f"SELECT order_id, strategy, symbol, pnl, entry_cost, exit_cost, {arm} AS arm, opened_at, closed_at "
        "FROM trades "
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
        f"x{r['quantity'] or 1} credit {credit_str} [{_arm(r)}]"
    )


def _earnings_costs(r) -> float:
    return (r["entry_cost"] or 0.0) + (r["exit_cost"] or 0.0)


def _fmt_earnings_exit(r) -> str:
    strat = (r["strategy"] or "spread").replace("_", " ")
    return (
        f"\U0001f534 Earnings paper EXIT — {r['symbol']} {strat} [{_arm(r)}], "
        f"P&L {_signed(_net('earnings', r))} (gross {_signed(r['pnl'])})"
    )


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
    return _embed(COLOR_ENTRY, f"OPEN · {r['symbol']} {strat}", details, footer=_arm(r))


def _embed_earnings_exit(r) -> dict:
    strat = (r["strategy"] or "spread").replace("_", " ")
    # Earnings' entry/exit costs are commission + pass-through + slippage: one cost figure here.
    details = (
        f"P&L {_signed(_net('earnings', r))} (gross {_signed(r['pnl'])}, costs ${_earnings_costs(r):.2f})"
    )
    entered, exited = _hhmm_epoch(r["opened_at"]), _hhmm_epoch(r["closed_at"])
    if entered or exited:
        details += f" · {entered or '?'} → {exited or '?'}"
    return _embed(COLOR_EXIT, f"CLOSE · {r['symbol']} {strat}", details, footer=_arm(r))


def _earnings_new_reviews(conn, notified_ids: set, floor: int = 0) -> list:
    """New per-symbol entry reviews (id watermark). Guarded — the table is absent on older DBs."""
    try:
        arm = _db.arm_column(conn, "entry_reviews")
        rows = conn.execute(
            "SELECT id, scan_date, symbol, timing, price, volume, winrate, winrate_sample, "
            "iv_rv_ratio, term_structure, market_cap, expected_move, best_tier, selected, reason, "
            f"{arm} AS arm FROM entry_reviews WHERE id > ? ORDER BY id",
            (floor,),
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


# How many near misses a summary names before it just counts the rest.
_NEAR_MISS_SHOWN = 8

# The earnings module's own soft criteria that a review row carries a figure for: the reject reason,
# the row's column, and the config key of the near-miss bar. A near miss is the module's definition,
# not a looser one: failed exactly one of these, while still clearing its near-miss bar -- the names
# a threshold change would actually admit. "Failed one check" alone listed RGS at 5,833 shares.
_SOFT = {
    "avg_volume_below_minimum": ("volume", "near_miss_min_avg_volume"),
    "winrate_below_minimum": ("winrate", "near_miss_min_winrate"),
    "iv_rv_ratio_below_minimum": ("iv_rv_ratio", "near_miss_min_iv_rv_ratio"),
    "market_cap_below_minimum": ("market_cap", "near_miss_min_market_cap"),
}
_EVALUATED = re.compile(r"\s*\(\d+ strateg(?:y|ies) evaluated\)\s*$")


def _reasons(r) -> list[str]:
    """The failed checks, without the "(6 strategies evaluated)" tail the last one carries -- left
    on, it split one check into two counts."""
    return [_EVALUATED.sub("", p).strip() for p in str(r["reason"] or "").split(";") if p.strip()]


def _near_miss_bars() -> dict[str, float]:
    """The earnings module's near-miss bars from its config's strategy defaults; empty when the
    config cannot be read, in which case a summary gives counts only."""
    try:
        from cherrypick.core import home as _home

        cfg = json.loads(_home.config_path("earnings").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    defaults = cfg.get("strategy_defaults") or {}
    return {
        key: float(defaults[key]) for _, key in _SOFT.values() if isinstance(defaults.get(key), (int, float))
    }


def _near_miss(r, bars: dict[str, float]) -> str | None:
    reasons = _reasons(r)
    if len(reasons) != 1 or reasons[0] not in _SOFT:
        return None
    column, key = _SOFT[reasons[0]]
    value = r[column]
    if value is None or key not in bars or value < bars[key]:
        return None
    shown = f"{int(value):,}" if column in ("volume", "market_cap") else f"{value:.2f}"
    return f"{r['symbol']} ({column} {shown}, {reasons[0]})"


def review_summaries(reviews, bars: dict[str, float] | None = None) -> dict[str, str]:
    """{scan_date: summary text} for the scans in this pass that rejected anything: how many were
    reviewed and chosen, the rejections counted by the check that failed, and the near misses in the
    earnings module's own sense (see `_SOFT`). Chosen names are pushed on their own and only counted
    here."""
    bars = _near_miss_bars() if bars is None else bars
    by_scan: dict[str, list] = {}
    for r in reviews:
        by_scan.setdefault(str(r["scan_date"] or "unknown"), []).append(r)
    out = {}
    for scan_date, rows in sorted(by_scan.items()):
        rejected = [r for r in rows if not r["selected"]]
        if not rejected:
            continue
        chosen = len(rows) - len(rejected)
        counts: dict[str, int] = {}
        for r in rejected:
            reasons = _reasons(r) or ["unrecorded"]
            # A name whose data could not be fetched fails every check as "..._unverified" -- eleven
            # entries in the counts for one missing chain. It is one fact: count it once.
            if all(x.endswith("_unverified") for x in reasons):
                reasons = ["data_unavailable"]
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
        near = [m for m in (_near_miss(r, bars) for r in rejected) if m]
        lines = [
            f"Earnings scan {scan_date}: {len(rows)} reviewed, {chosen} chosen, {len(rejected)} rejected.",
            "Rejected by check: "
            + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        ]
        if near:
            more = f" and {len(near) - _NEAR_MISS_SHOWN} more" if len(near) > _NEAR_MISS_SHOWN else ""
            lines.append("Near misses: " + "; ".join(near[:_NEAR_MISS_SHOWN]) + more)
        lines.append("Each rejection is in the notification log in full.")
        out[scan_date] = "\n".join(lines)
    return out


def _fmt_earnings_review(r) -> str:
    """Per-symbol review summary — the data reviewed for entry plus the chosen/rejected decision, in the
    bullet layout the account owner asked for."""
    icon = "\U0001f7e2" if r["selected"] else "⚪"  # green vs white circle
    decision = "chosen" if r["selected"] else "rejected"
    timing = f" ({r['timing']})" if r["timing"] else ""
    head = f"{icon} Earnings review — {r['symbol']}{timing}: {decision} — {r['reason']} [{_arm(r)}]"
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
        footer=_arm(r),
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
    present = _earnings_present(conn)
    st["notified_entry_ids"] = _pruned(entered, present and present[0])

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
    st["notified_exit_ids"] = _pruned(notified, present and present[1])

    # Per-symbol entry reviews: the data reviewed for each symbol during the entry scan + the
    # chosen/rejected decision, id-watermarked like entries/exits. A CHOSEN review is pushed on its
    # own; a REJECTED one is written whole to the log floor and folded into one summary per scan.
    # Until 2026-09-27 every rejection was its own push: 1,054 of 1,103 review notices in two months
    # (up to 87 a day), burying the 49 that opened trades.
    reviewed = set(st.get("notified_review_ids", []))
    if "review_floor_id" not in st:
        # Before floors the list was cut in no particular order, so only an uncut one is complete.
        st["review_floor_id"] = 0 if len(reviewed) < _ID_CAP else max(reviewed)
    reviews = _earnings_new_reviews(conn, reviewed, st["review_floor_id"])
    rejected = []
    for r in reviews:
        key = f"trade.{name}.review.{r['id']}"
        if r["selected"]:
            notifier.notify(
                "INFO", key, "Earnings review", _fmt_earnings_review(r), embed=_embed_earnings_review(r)
            )
        else:
            notifier.record("INFO", key, "Earnings review", _fmt_earnings_review(r))
            rejected.append(r)
        reviewed.add(r["id"])
    st["notified_review_ids"], st["review_floor_id"] = _trim_ids(reviewed, st["review_floor_id"])
    summaries = review_summaries(reviews)
    for scan_date, text in summaries.items():
        notifier.notify("INFO", f"trade.{name}.review_summary.{scan_date}", "Earnings scan", text)

    return {
        "entries_notified": len(entries),
        "exits_notified": len(exits),
        "reviews_notified": len(reviews) - len(rejected),
        "reviews_recorded": len(rejected),
        "review_summaries": len(summaries),
    }


# --------------------------------------------------------------------------- staged text-keyed schemas
def _position_id(r) -> str:
    return r["position_id"]


def _run_stages(conn, st: dict, notifier: Notifier, name: str, stages: list, rows_fn=None) -> dict:
    """One pass over a text-keyed schema's notifiable stages. Each stage is (state key, event,
    title, formatter, embed builder, query[, id function]); a row whose id is not yet remembered
    under the key is pushed once and remembered.

    The remembered ids are pruned to the ids the stage's own query still returns, never cut to a
    count: a count cap forgets ids the query keeps returning, and a forgotten id is pushed again
    (see `_trim_ids`). The list is therefore bounded by the ledger itself."""
    counts = {}
    for stage in stages:
        key, event, title, fmt, embed_fn, query = stage[:6]
        id_of = stage[6] if len(stage) > 6 else _position_id
        all_rows = rows_fn(conn, query) if rows_fn else conn.execute(query).fetchall()
        present = {id_of(r) for r in all_rows}
        notified = set(st.get(key, []))
        rows = [r for r in all_rows if id_of(r) not in notified]
        for r in rows:
            notifier.notify("INFO", f"trade.{name}.{event}.{id_of(r)}", title, fmt(r), embed=embed_fn(r))
            notified.add(id_of(r))
        st[key] = sorted(notified & present)
        counts[f"{event}s_notified"] = len(rows)
    return counts


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
    return _run_stages(conn, st, notifier, name, stages)


# cherrypick-calendars keys on a text `position_id` and lives across a week, so a position has
# three notifiable moments: it opens (Monday's double-calendar entry), its short leg may cash-settle
# at the Friday bell (the position survives as a long into the weekend — worth a distinct ping,
# since it is the one moment the structure's shape changes without a trade), and it finally closes
# (the control's scheduled Friday exit, an advised trigger, or the Monday long disposition).
def _calendars_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, settlement_spot, status FROM dc_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_settlement_ids": [r["position_id"] for r in rows if r["settlement_spot"] is not None],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "closed"],
    }


def _fmt_calendars_entry(r) -> str:
    return (
        f"\U0001f7e2 Calendars paper ENTRY — {r['symbol']} {r['structure']} {r['side']} calendar "
        f"{r['strike']:.0f} for ${r['entry_debit']:.2f} debit (EM {r['entry_em']:.1f}) [{_arm(r)}]"
    )


def _embed_calendars_entry(r) -> dict:
    details = (
        f"{r['side']} {r['strike']:.0f} · short {r['front_expiration']} / long {r['back_expiration']} "
        f"· debit ${r['entry_debit']:.2f} · spot {r['entry_spot']:.2f} ± EM {r['entry_em']:.1f}"
    )
    title = f"ENTRY · {r['symbol']} {r['structure']} {r['side']} {r['strike']:.0f}"[:256]
    return _embed(COLOR_ENTRY, title, details, footer=_arm(r))


def _fmt_calendars_settlement(r) -> str:
    itm = "ITM" if (r["itm_settlements"] or 0) > 0 else "OTM"
    return (
        f"⚖️ Calendars SHORT SETTLED — {r['symbol']} {r['side']} {r['strike']:.0f} "
        f"{itm} at {r['settlement_spot']:.2f}; the long rides to {r['back_expiration']} [{_arm(r)}]"
    )


def _embed_calendars_settlement(r) -> dict:
    details = (
        f"settled {r['settlement_spot']:.2f} · {r['itm_settlements'] or 0} ITM · "
        f"long open to {r['back_expiration']}"
    )
    title = f"SHORT SETTLED · {r['symbol']} {r['side']} {r['strike']:.0f}"[:256]
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _fmt_calendars_exit(r) -> str:
    net = _net("dc_week", r)
    return (
        f"\U0001f3c1 Calendars paper CLOSED — {r['symbol']} {r['structure']} {r['side']} "
        f"{r['strike']:.0f} net {_signed(net)} ({r['exit_reason']}) [{_arm(r)}]"
    )


def _embed_calendars_exit(r) -> dict:
    net = _net("dc_week", r)
    details = (
        f"net {_signed(net)} (gross {_signed(r['gross_pnl'])}, fees ${r['fees'] or 0:.2f}) · "
        f"{r['exit_reason']} · week {r['week_of']}"
    )
    title = f"CLOSED · {r['symbol']} {r['structure']} {r['side']} {r['strike']:.0f}"[:256]
    return _embed(COLOR_EXIT, title, details, footer=_arm(r))


def _calendars_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    stages = [
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_calendars_entry,
            _embed_calendars_entry,
            "SELECT * FROM dc_positions",
        ),
        (
            "notified_settlement_ids",
            "settlement",
            "Short settled",
            _fmt_calendars_settlement,
            _embed_calendars_settlement,
            "SELECT * FROM dc_positions WHERE settlement_spot IS NOT NULL",
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper closed",
            _fmt_calendars_exit,
            _embed_calendars_exit,
            "SELECT * FROM dc_positions WHERE status = 'closed'",
        ),
    ]
    return _run_stages(conn, st, notifier, name, stages)


# cherrypick-pmcc keys on a text `position_id`. A weekly (control) position lives ~1-2 weeks; a
# held-long one ~10 months, selling a weekly short against its long. Notifiable moments: it opens; a
# NOTABLE short trade (an early roll on decay or breach, a deadline or ex-dividend buyback -- the
# routine Friday roll is not pinged, or a held-long arm would ping every week); a short settling at a
# bell (keyed per LEG, since a held-long position can settle many); and it closes.
#
# Rolls and buybacks are read from `pmcc_management_events`, which carries WHY; the position row only
# says how many. Both newer keys are seeded from the ledger the first time a pass finds them missing
# from an existing state, so a deploy never replays the settlements and rolls already on file.
_PMCC_ROLL_SQL = (
    "SELECT e.id AS event_id, e.action AS event_action, e.reason AS event_reason, e.detail_json, p.* "
    "FROM pmcc_management_events e JOIN pmcc_positions p ON p.position_id = e.position_id "
    "WHERE e.executed = 1 "
    "AND ((e.action = 'roll_short' AND e.reason != 'expiry') OR e.action = 'close_short')"
)
_PMCC_SETTLED_SQL = (
    "SELECT l.leg_role, l.strike AS leg_strike, l.expiration AS leg_expiration, l.close_kind, "
    "l.close_spot, p.* FROM pmcc_legs l JOIN pmcc_positions p ON p.position_id = l.position_id "
    "WHERE l.status = 'settled' AND l.leg_role != 'long_call'"
)


def _pmcc_rows(conn, sql: str) -> list:
    """Rows, or none from a ledger that predates the table (an old or hand-built one)."""
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:
        if str(exc).startswith("no such table"):
            return []
        raise


def _pmcc_event_id(r) -> str:
    return f"{r['position_id']}:{r['event_id']}"


def _pmcc_leg_id(r) -> str:
    return f"{r['position_id']}:{r['leg_role']}"


def _pmcc_new_keys(conn) -> dict:
    return {
        "notified_roll_event_ids": [_pmcc_event_id(r) for r in _pmcc_rows(conn, _PMCC_ROLL_SQL)],
        "notified_settled_leg_ids": [_pmcc_leg_id(r) for r in _pmcc_rows(conn, _PMCC_SETTLED_SQL)],
    }


def _pmcc_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, status FROM pmcc_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "closed"],
        **_pmcc_new_keys(conn),
    }


def _fmt_pmcc_entry(r) -> str:
    return (
        f"\U0001f7e2 PMCC paper ENTRY — {r['symbol']} long {r['long_strike']:.0f} "
        f"({r['long_expiration']}) / short {r['short_strike']:.0f} ({r['short_expiration']}) "
        f"for ${r['net_debit']:.2f} debit, TV ${r['entry_net_tv'] or 0:.2f} [{_arm(r)}]"
    )


def _embed_pmcc_entry(r) -> dict:
    details = (
        f"long {r['long_strike']:.0f} {r['long_expiration']} / short {r['short_strike']:.0f} "
        f"{r['short_expiration']} · debit ${r['net_debit']:.2f} · net TV ${r['entry_net_tv'] or 0:.2f} "
        f"· protection {(r['entry_downside_protection_pct'] or 0) * 100:.1f}% · spot {r['entry_spot']:.2f}"
    )
    title = f"ENTRY · {r['symbol']} PMCC {r['long_strike']:.0f}/{r['short_strike']:.0f}"[:256]
    return _embed(COLOR_ENTRY, title, details, footer=_arm(r))


def _pmcc_detail(r) -> dict:
    try:
        return json.loads(r["detail_json"] or "{}")
    except ValueError:
        return {}


def _fmt_pmcc_roll(r) -> str:
    d = _pmcc_detail(r)
    if r["event_action"] == "close_short":
        return (
            f"\u23f9 PMCC SHORT BOUGHT BACK — {r['symbol']} {d.get('strike', 0):.0f} "
            f"({r['event_reason']}); no short until the next sale [{_arm(r)}]"
        )
    return (
        f"\U0001f504 PMCC ROLLED ({r['event_reason']}) — {r['symbol']} {d.get('old_strike', 0):.0f} → "
        f"{d.get('new_strike', 0):.0f} ({d.get('new_expiration', '?')}), net "
        f"{d.get('net_roll_credit', 0) or 0:+.2f}/sh [{_arm(r)}]"
    )


def _embed_pmcc_roll(r) -> dict:
    d = _pmcc_detail(r)
    if r["event_action"] == "close_short":
        title = f"BOUGHT BACK · {r['symbol']} PMCC short {d.get('strike', 0):.0f}"
        body = f"{r['event_reason']} · no short until the next sale"
        return _embed(COLOR_COMPLETE, title, body, footer=_arm(r))
    details = (
        f"{d.get('old_strike', 0):.0f} {d.get('old_expiration', '?')} → {d.get('new_strike', 0):.0f} "
        f"{d.get('new_expiration', '?')} · {r['event_reason']} · "
        f"net {d.get('net_roll_credit', 0) or 0:+.2f}/sh"
    )
    title = f"ROLLED · {r['symbol']} PMCC short {d.get('new_strike', 0):.0f}"
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _pmcc_after_settlement(r) -> str:
    """What happens to the long after its short settled -- which differs by lifecycle."""
    if r["status"] == "short_settled":
        return "the long rides to the next session's disposal"
    if r["status"] == "open":
        return "the long stays open; the next short sells next session"
    return "the position closed"


def _fmt_pmcc_settlement(r) -> str:
    itm = {"assigned": "ITM (shares delivered)", "cash_settled": "ITM (cash)"}.get(r["close_kind"], "OTM")
    spot = r["close_spot"] if r["close_spot"] is not None else r["settlement_spot"]
    return (
        f"⚖️ PMCC SHORT SETTLED — {r['symbol']} {r['leg_strike']:.0f} {itm} "
        f"at {(spot or 0):.2f}; {_pmcc_after_settlement(r)} [{_arm(r)}]"
    )


def _embed_pmcc_settlement(r) -> dict:
    spot = r["close_spot"] if r["close_spot"] is not None else r["settlement_spot"]
    details = f"settled {(spot or 0):.2f} · {r['close_kind']} · {_pmcc_after_settlement(r)}"
    title = f"SHORT SETTLED · {r['symbol']} PMCC {r['leg_strike']:.0f}"[:256]
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _fmt_pmcc_exit(r) -> str:
    net = _net("pmcc", r)
    return (
        f"\U0001f3c1 PMCC paper CLOSED — {r['symbol']} "
        f"{r['long_strike']:.0f}/{r['short_strike']:.0f} net {_signed(net)} "
        f"({r['exit_reason']}) [{_arm(r)}]"
    )


def _embed_pmcc_exit(r) -> dict:
    net = _net("pmcc", r)
    details = (
        f"net {_signed(net)} (gross {_signed(r['gross_pnl'])}, fees ${r['fees'] or 0:.2f}) · "
        f"{r['exit_reason']} · entered {r['entry_session']}"
    )
    title = f"CLOSED · {r['symbol']} PMCC {r['long_strike']:.0f}/{r['short_strike']:.0f}"[:256]
    return _embed(COLOR_EXIT, title, details, footer=_arm(r))


def _pmcc_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    # Keys introduced after a module's state was first seeded start from the ledger as it stands.
    for key, ids in _pmcc_new_keys(conn).items():
        st.setdefault(key, ids)
    stages = [
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_pmcc_entry,
            _embed_pmcc_entry,
            "SELECT * FROM pmcc_positions",
            lambda r: r["position_id"],
        ),
        (
            "notified_roll_event_ids",
            "roll",
            "Short rolled",
            _fmt_pmcc_roll,
            _embed_pmcc_roll,
            _PMCC_ROLL_SQL,
            _pmcc_event_id,
        ),
        (
            "notified_settled_leg_ids",
            "settlement",
            "Short settled",
            _fmt_pmcc_settlement,
            _embed_pmcc_settlement,
            _PMCC_SETTLED_SQL,
            _pmcc_leg_id,
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper closed",
            _fmt_pmcc_exit,
            _embed_pmcc_exit,
            "SELECT * FROM pmcc_positions WHERE status = 'closed'",
            lambda r: r["position_id"],
        ),
    ]
    return _run_stages(conn, st, notifier, name, stages, rows_fn=_pmcc_rows)


# cherrypick-curve keys on a text `position_id` and holds ~30-45 DTE with three notifiable
# moments: it opens, its short call may settle at expiry (assignment delivers short shares that
# ride to the next session's disposal), and it closes (profit-take, regime-flip hard exit, or
# close_dte). No roll — the calendars three-stage shape, not pmcc's four.
def _curve_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, settlement_spot, status FROM curve_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_settlement_ids": [r["position_id"] for r in rows if r["settlement_spot"] is not None],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "closed"],
    }


def _fmt_curve_entry(r) -> str:
    return (
        f"\U0001f7e2 Curve paper ENTRY — {r['symbol']} short {r['short_strike']:.0f}C / "
        f"long {r['long_strike']:.0f}C for ${r['entry_credit']:.2f} credit [{_arm(r)}]"
    )


def _embed_curve_entry(r) -> dict:
    details = (
        f"short {r['short_strike']:.0f}C / long {r['long_strike']:.0f}C {r['expiration']} · "
        f"credit ${r['entry_credit']:.2f} · spot {r['entry_spot'] or 0:.2f}"
    )
    title = f"ENTRY · {r['symbol']} curve {r['short_strike']:.0f}/{r['long_strike']:.0f}"[:256]
    return _embed(COLOR_ENTRY, title, details, footer=_arm(r))


def _fmt_curve_settlement(r) -> str:
    itm = "ITM (shares delivered)" if (r["itm_settlements"] or 0) > 0 else "OTM"
    return (
        f"⚖️ Curve SHORT SETTLED — {r['symbol']} {r['short_strike']:.0f}C {itm} "
        f"at {r['settlement_spot']:.2f}; the long rides to the next session [{_arm(r)}]"
    )


def _embed_curve_settlement(r) -> dict:
    details = f"settled {r['settlement_spot']:.2f} · {r['itm_settlements'] or 0} ITM"
    title = f"SHORT SETTLED · {r['symbol']} curve {r['short_strike']:.0f}"[:256]
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _fmt_curve_exit(r) -> str:
    net = _net("curve_vx", r)
    return (
        f"\U0001f3c1 Curve paper CLOSED — {r['symbol']} "
        f"{r['short_strike']:.0f}/{r['long_strike']:.0f} net {_signed(net)} "
        f"({r['exit_reason']}) [{_arm(r)}]"
    )


def _embed_curve_exit(r) -> dict:
    net = _net("curve_vx", r)
    details = (
        f"net {_signed(net)} (gross {_signed(r['gross_pnl'])}, fees ${r['fees'] or 0:.2f}) · "
        f"{r['exit_reason']} · entered {r['entry_session']}"
    )
    title = f"CLOSED · {r['symbol']} curve {r['short_strike']:.0f}/{r['long_strike']:.0f}"[:256]
    return _embed(COLOR_EXIT, title, details, footer=_arm(r))


def _curve_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    stages = [
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_curve_entry,
            _embed_curve_entry,
            "SELECT * FROM curve_positions",
        ),
        (
            "notified_settlement_ids",
            "settlement",
            "Short settled",
            _fmt_curve_settlement,
            _embed_curve_settlement,
            "SELECT * FROM curve_positions WHERE settlement_spot IS NOT NULL",
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper closed",
            _fmt_curve_exit,
            _embed_curve_exit,
            "SELECT * FROM curve_positions WHERE status = 'closed'",
        ),
    ]
    return _run_stages(conn, st, notifier, name, stages)


# cherrypick-contango keys on a text `position_id`, one per holding stint, with two notifiable
# moments: a stint opens (the arm bought a fund) and it closes (the arm sold it on a switch). Every
# switch is both, so one switch reads as a close and an entry on the same tick.
def _contango_seed(conn) -> dict:
    rows = conn.execute("SELECT position_id, status FROM contango_positions").fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "closed"],
    }


def _fmt_contango_entry(r) -> str:
    return (
        f"🟢 Contango paper SWITCH IN — bought {r['shares']} {r['symbol']} @ {r['entry_mid']:.2f} "
        f"({r['role']}, VIX/VIX3M {r['entry_ratio']:.3f}) [{_arm(r)}]"
    )


def _embed_contango_entry(r) -> dict:
    details = (
        f"{r['shares']} {r['symbol']} @ {r['entry_mid']:.2f} · ratio {r['entry_ratio']:.3f} · {r['role']}"
    )
    return _embed(COLOR_ENTRY, f"SWITCH IN · {r['symbol']} contango"[:256], details, footer=_arm(r))


def _fmt_contango_exit(r) -> str:
    return (
        f"🏁 Contango paper SWITCH OUT — sold {r['shares']} {r['symbol']} @ {r['exit_mid']:.2f}, "
        f"net {_signed(_net('contango_etf', r))} ({r['exit_reason']}) [{_arm(r)}]"
    )


def _embed_contango_exit(r) -> dict:
    details = (
        f"net {_signed(_net('contango_etf', r))} (gross {_signed(r['gross_pnl'])}, "
        f"fees ${r['fees'] or 0:.2f}, slippage ${r['slippage'] or 0:.2f}) · held since {r['entry_session']}"
    )
    return _embed(COLOR_EXIT, f"SWITCH OUT · {r['symbol']} contango"[:256], details, footer=_arm(r))


def _contango_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    stages = [
        (
            "notified_exit_ids",
            "exit",
            "Paper closed",
            _fmt_contango_exit,
            _embed_contango_exit,
            "SELECT * FROM contango_positions WHERE status = 'closed'",
        ),
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_contango_entry,
            _embed_contango_entry,
            "SELECT * FROM contango_positions",
        ),
    ]
    return _run_stages(conn, st, notifier, name, stages)


# cherrypick-bwb keys on a text `position_id` and ladders daily like pmcc, with four notifiable
# moments: it opens (the base 1-3-2 put broken-wing fly), its add-on may FIRE (the reversal
# trigger — keyed by position_id since only one add-on ever fires per position), its short legs
# may settle at expiry, and it closes.
def _bwb_seed(conn) -> dict:
    rows = conn.execute(
        "SELECT position_id, addon_fired_at, settlement_spot, status FROM bwb_positions"
    ).fetchall()
    return {
        "notified_entry_ids": [r["position_id"] for r in rows],
        "notified_addon_ids": [r["position_id"] for r in rows if r["addon_fired_at"] is not None],
        "notified_settlement_ids": [r["position_id"] for r in rows if r["settlement_spot"] is not None],
        "notified_exit_ids": [r["position_id"] for r in rows if r["status"] == "closed"],
    }


def _fmt_bwb_entry(r) -> str:
    return (
        f"\U0001f7e2 BWB paper ENTRY — {r['symbol']} put fly body {r['body_strike']:.0f} "
        f"near {r['near_strike']:.0f} / far {r['far_strike']:.0f} for ${r['entry_credit']:.2f} "
        f"credit [{_arm(r)}]"
    )


def _embed_bwb_entry(r) -> dict:
    details = (
        f"body {r['body_strike']:.0f} · near {r['near_strike']:.0f} / far {r['far_strike']:.0f} "
        f"{r['expiration']} · credit ${r['entry_credit']:.2f} · spot {r['entry_spot'] or 0:.2f}"
    )
    title = f"ENTRY · {r['symbol']} BWB {r['body_strike']:.0f}"[:256]
    return _embed(COLOR_ENTRY, title, details, footer=_arm(r))


def _fmt_bwb_addon(r) -> str:
    return (
        f"\U0001f504 BWB ADD-ON FIRED — {r['symbol']} credit spread "
        f"{r['addon_short_strike']:.0f}/{r['addon_long_strike']:.0f} for ${r['addon_credit']:.2f} "
        f"credit, now a 1-3-2 [{_arm(r)}]"
    )


def _embed_bwb_addon(r) -> dict:
    details = (
        f"add-on {r['addon_short_strike']:.0f}/{r['addon_long_strike']:.0f} · credit ${r['addon_credit']:.2f}"
    )
    title = f"ADD-ON FIRED · {r['symbol']} BWB {r['body_strike']:.0f}"[:256]
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _fmt_bwb_settlement(r) -> str:
    itm = "ITM (shares delivered)" if (r["itm_settlements"] or 0) > 0 else "OTM"
    return (
        f"⚖️ BWB SETTLED — {r['symbol']} body {r['body_strike']:.0f} {itm} "
        f"at {r['settlement_spot']:.2f} [{_arm(r)}]"
    )


def _embed_bwb_settlement(r) -> dict:
    details = f"settled {r['settlement_spot']:.2f} · {r['itm_settlements'] or 0} ITM"
    title = f"SETTLED · {r['symbol']} BWB {r['body_strike']:.0f}"[:256]
    return _embed(COLOR_COMPLETE, title, details, footer=_arm(r))


def _fmt_bwb_exit(r) -> str:
    net = _net("bwb_132", r)
    return (
        f"\U0001f3c1 BWB paper CLOSED — {r['symbol']} body {r['body_strike']:.0f} "
        f"net {_signed(net)} ({r['exit_reason']}) [{_arm(r)}]"
    )


def _embed_bwb_exit(r) -> dict:
    net = _net("bwb_132", r)
    details = (
        f"net {_signed(net)} (gross {_signed(r['gross_pnl'])}, fees ${r['fees'] or 0:.2f}) · "
        f"{r['exit_reason']} · entered {r['entry_session']}"
    )
    title = f"CLOSED · {r['symbol']} BWB {r['body_strike']:.0f}"[:256]
    return _embed(COLOR_EXIT, title, details, footer=_arm(r))


def _bwb_process(conn, st: dict, notifier: Notifier, name: str) -> dict:
    stages = [
        (
            "notified_entry_ids",
            "entry",
            "Paper entry",
            _fmt_bwb_entry,
            _embed_bwb_entry,
            "SELECT * FROM bwb_positions",
        ),
        (
            "notified_addon_ids",
            "addon",
            "Add-on fired",
            _fmt_bwb_addon,
            _embed_bwb_addon,
            "SELECT * FROM bwb_positions WHERE addon_fired_at IS NOT NULL",
        ),
        (
            "notified_settlement_ids",
            "settlement",
            "Short settled",
            _fmt_bwb_settlement,
            _embed_bwb_settlement,
            "SELECT * FROM bwb_positions WHERE settlement_spot IS NOT NULL",
        ),
        (
            "notified_exit_ids",
            "exit",
            "Paper closed",
            _fmt_bwb_exit,
            _embed_bwb_exit,
            "SELECT * FROM bwb_positions WHERE status = 'closed'",
        ),
    ]
    return _run_stages(conn, st, notifier, name, stages)


# Registry: paper.trade_schema -> (seed_fn, process_fn). Schemas not listed here skip cleanly.
_SCHEMAS = {
    "meic_ic": (_meic_seed, _meic_process),
    "earnings": (_earnings_seed, _earnings_process),
    "fly_book": (_flies_seed, _flies_process),
    "dc_week": (_calendars_seed, _calendars_process),
    "pmcc": (_pmcc_seed, _pmcc_process),
    "curve_vx": (_curve_seed, _curve_process),
    "bwb_132": (_bwb_seed, _bwb_process),
    "contango_etf": (_contango_seed, _contango_process),
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
def _process_isolated(process_fn, conn, st: dict, notifier, name: str, **kwargs) -> dict:
    """One module's pass, failing on its own. The state is saved once, after every module, so a
    module that raised used to take everyone's save down with it -- and each module had already
    SENT its events, so the next pass sent them all again. On 2026-09-24 pmcc and bwb raised on a
    renamed column from 09:31 ET to the close and every flies event went out ~95 times. The error is
    kept on the module's own state as well as in the summary, because the watchdog calls this
    inside a bare except and a summary alone is read by nobody."""
    try:
        out = process_fn(conn, st, notifier, name, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- one module's defect must not replay every other's
        err = f"{type(exc).__name__}: {exc}"
        st["last_error"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "error": err}
        return {"error": err}
    st.pop("last_error", None)
    return out


class _DryRunNotifier:
    """Formats, records, sends nothing. What `run(dry_run=True)` hands every module in place of the
    real notifier, so a pass can be rehearsed against a ledger -- a copy after a schema change, say --
    with every formatter exercised and no message leaving the machine."""

    def __init__(self):
        self.sent: list[dict] = []

    def notify(self, level: str, key: str, title: str, message: str, embed: dict | None = None):
        self.sent.append({"key": key, "title": title})


def run(cfg: dict | None = None, *, dry_run: bool = False) -> dict:
    """One notification pass. `dry_run` runs every module's formatters against the current state and
    reports what WOULD be sent, per module, without sending or saving anything -- the state is a
    copy, so the next real pass behaves exactly as if this one never ran."""
    if not _acquire_lock():
        # Another invocation (the 2-min task vs the watchdog tick) is mid-run; racing it
        # would replay its already-notified ids. Skip — the next tick covers us.
        return {"ok": True, "skipped": "another trade-notify run holds the lock"}
    try:
        cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
        notify_cfg = cfg.get("notify", {})
        channels = notify_cfg.get("trade_channels", ["log"])
        recorder = _DryRunNotifier() if dry_run else None
        notifier = recorder if recorder is not None else Notifier({**notify_cfg, "channels": channels})
        summary_cfg = notify_cfg.get("trade_summary", {})
        # mode "summary" routes EVERY trade to the digest regardless of profile; the empty prefix
        # matches every arm (str.startswith("") is always True, and the day-totals query's
        # LIKE '%' matches every row). "per-trade" (the default) keeps the prefix routing.
        if summary_cfg.get("mode", "per-trade") == "summary":
            summary_prefixes: tuple[str, ...] = ("",)
        else:
            summary_prefixes = tuple(summary_cfg.get("profile_prefixes", []))
        summary_interval_minutes = summary_cfg.get("interval_minutes", 15)

        state = json.loads(json.dumps(_load_state())) if dry_run else _load_state()
        loaded = _dumps(state)
        summary: dict[str, Any] = {}
        today = timeutil.et_from_epoch(time.time()).date()
        wanted: set[str] = set()  # state keys a module is still configured to notify under

        for name, mcfg in cfgmod.enabled_modules(cfg).items():
            paper = mcfg.get("paper", {})
            if not paper.get("notify_trades"):
                continue
            schema = schemas.canonical(paper.get("trade_schema", "meic_ic"))
            adapter = _SCHEMAS.get(schema)
            if adapter is None:  # unknown schema — skip cleanly
                continue
            wanted.add(name)
            db_path = cfgmod.paper_db_path(mcfg, name)
            if not db_path.exists():
                continue
            seed_fn, process_fn = adapter

            conn = _connect_ro(db_path)
            try:
                st = state.get(name)
                if st is None or _stale(st, today):  # first or renewed activation — seed, don't backfill
                    summary[name] = {"seeded": True, **({"reseeded": "stale"} if st is not None else {})}
                    state[name] = {**seed_fn(conn), "processed_on": today.isoformat()}
                    continue
                st["processed_on"] = today.isoformat()
                if schema == "meic_ic":
                    kwargs = {
                        "summary_prefixes": summary_prefixes,
                        "summary_interval_minutes": summary_interval_minutes,
                    }
                else:
                    kwargs = {}
                summary[name] = _process_isolated(process_fn, conn, st, notifier, name, **kwargs)
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
            schema = schemas.canonical(mcfg.get("paper", {}).get("trade_schema", "meic_ic"))
            adapter = _SCHEMAS.get(schema)
            if adapter is None:
                continue
            key = f"{name}:live"
            wanted.add(key)
            db_path = cfgmod.live_db_path(mcfg, name)
            if db_path is None or not db_path.exists():
                continue
            seed_fn, process_fn = adapter
            live_channels = sorted(set(channels) | {"desktop"})
            live_notifier = _LiveNotifier(
                recorder if recorder is not None else Notifier({**notify_cfg, "channels": live_channels})
            )
            conn = _connect_ro(db_path)
            try:
                st = state.get(key)
                if st is None or _stale(st, today):  # first or renewed activation — seed, don't backfill
                    summary[key] = {"seeded": True, **({"reseeded": "stale"} if st is not None else {})}
                    state[key] = {**seed_fn(conn), "processed_on": today.isoformat()}
                    continue
                st["processed_on"] = today.isoformat()
                if schema == "meic_ic":
                    kwargs = {"summary_prefixes": (), "summary_interval_minutes": summary_interval_minutes}
                else:
                    kwargs = {}
                summary[key] = _process_isolated(process_fn, conn, st, live_notifier, name, **kwargs)
            finally:
                conn.close()

        if recorder is not None:
            return {"ok": True, "dry_run": True, "modules": summary, "would_send": recorder.sent}
        # A module no longer notified keeps no state: it was dead weight rewritten every pass (MEIC's
        # was 3.9 MB of a 7.3 MB file), and switching the module back on seeds it afresh.
        pruned = sorted(k for k in state if k not in wanted)
        for k in pruned:
            del state[k]
        written = _save_state(state, unchanged_from=loaded)
        out: dict[str, Any] = {"ok": True, "modules": summary, "written": written}
        if pruned:
            out["pruned"] = pruned
        return out
    finally:
        _release_lock()
