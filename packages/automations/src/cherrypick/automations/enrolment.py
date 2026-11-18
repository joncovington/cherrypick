"""Taking an automation to paper: its start date, what of it a backtest has judged, and the forward-check
rule its paper record is read by. Pure functions over an arm.

**Start.** `start` (ISO date) is the first session the paper loop may enter it; a backtest ignores it,
since a backtest is not a forward test. A member of a group whose `groups.<name>.start` is later joins
then (`config.apply_groups`): a group is judged from one boundary.

**Blocks and labels.** An automation is five blocks (`BLOCKS`): the structure (symbol, expiry, legs),
the entry (entry, weekdays, event skips), the gates, the exit, and the size (size and limits). A
register label may carry `blocks`, each block's hash (`block_hash`) as it was when the study ran. A
block is `backtested` while its hash still matches, `changed` once it does not (the verdict no longer
speaks for it), and `not_backtested` with no hash at all. So a paper result is labelled for what it is,
block by block: "entry backtested, exit not".

**Backtest suggestion.** An automation with a block not backtested gets `suggestion`: what its blocks
read (`needs`: the chain at its expiry class and the entry's cadence, the greeks when a leg is placed by
delta, the spot trail for an intraday trigger) and, from the source registry's inventory
(`core.sources.coverage`), whether each is covered over the `SUGGEST_DAYS` up to the last finished
session. Our own stores are asked first; a licensed (vendor) store only where they fall short and the
caller says the licence allows new work (`licensed_ok`, which `paper_loop.backtest_suggestions` reads
through `licence.allows_new_work`, the driver's own test). A need a licensed store covers is marked
`licensed`, and so is the suggestion, since its result will be. Where every need is covered the suite
says the backtest can run; it never runs one.

**Forward rule.** `forward` declares, at enrolment and before any paper is read, how the paper record
is judged, in the shape the orchestrator's `forward-checks` reads (`forwardcheck` there):
`measure`, `field`, `min_obs`, `kill_z`, `drift_z`, optionally `per`, `reference` (default null: paper
judged on its own) and `beside`. `forward_rows` fills in the module, the ledger schema, the arm and
`since` (the start), so the paper results become evidence without a separate declaration.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from cherrypick.core import calendar as cal
from cherrypick.core import sources as src

from .model import hhmm

BLOCKS: dict[str, tuple[str, ...]] = {
    "structure": ("symbol", "expiry", "legs"),
    "entry": ("entry", "weekdays", "skip_events"),
    "gates": ("gates",),
    "exit": ("exit",),
    "size": ("size", "limits"),
}
BACKTESTED, CHANGED, NOT_BACKTESTED = "backtested", "changed", "not_backtested"

FORWARD_KEYS = frozenset({"measure", "field", "per", "min_obs", "kill_z", "drift_z", "reference", "beside"})
FORWARD_NEEDED = ("measure", "field", "min_obs", "kill_z", "drift_z")
MEASURES = ("cycle_mean", "session_sum")
MODULE, SCHEMA = "automations", "ensemble_trade"


def block_hash(arm: dict, block: str) -> str:
    """The first 12 hex digits of sha256 over the block's keys as canonical JSON (a key the arm leaves
    out is absent, not null)."""
    part = {k: arm[k] for k in BLOCKS[block] if k in arm}
    text = json.dumps(part, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def block_hashes(arm: dict) -> dict[str, str]:
    return {b: block_hash(arm, b) for b in BLOCKS}


def labels(arm: dict) -> dict[str, dict]:
    """{block: {"state": backtested|changed|not_backtested, "item", "verdict"}}."""
    reg = arm.get("register") or {}
    recorded = reg.get("blocks") or {}
    out = {}
    for b in BLOCKS:
        if b not in recorded:
            state = NOT_BACKTESTED
        else:
            state = BACKTESTED if recorded[b] == block_hash(arm, b) else CHANGED
        row = {"state": state}
        if state != NOT_BACKTESTED:
            row.update(item=reg.get("item"), verdict=reg.get("verdict"))
        out[b] = row
    return out


def summary(arm: dict) -> str:
    """One line: "structure, entry backtested (item 75, passed); exit changed; size not backtested"."""
    by: dict[str, list[str]] = {}
    for b, row in labels(arm).items():
        by.setdefault(row["state"], []).append(b)
    reg = arm.get("register") or {}
    parts = []
    if by.get(BACKTESTED):
        parts.append(f"{', '.join(by[BACKTESTED])} backtested (item {reg.get('item')}, {reg.get('verdict')})")
    if by.get(CHANGED):
        parts.append(f"{', '.join(by[CHANGED])} changed since its backtest")
    if by.get(NOT_BACKTESTED):
        parts.append(f"{', '.join(by[NOT_BACKTESTED])} not backtested")
    return "; ".join(parts)


def _iso(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return len(v) == 10


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def problems(arm: dict) -> list[str]:
    """The arm's `start`, `forward` and `register.blocks` problems, each with its path."""
    name = arm.get("name", "?")
    p = []
    if "start" in arm and not _iso(arm["start"]):
        p.append(f"{name}.start: an ISO date, YYYY-MM-DD")
    blocks = (arm.get("register") or {}).get("blocks")
    if blocks is not None:
        if not isinstance(blocks, dict) or set(blocks) - set(BLOCKS):
            p.append(f"{name}.register.blocks: {{block: hash}} for blocks {sorted(BLOCKS)}")
        elif not all(isinstance(v, str) and len(v) == 12 for v in blocks.values()):
            p.append(f"{name}.register.blocks: each a 12-digit hash (`enrolment.block_hash`)")
    fw = arm.get("forward")
    if fw is None:
        return p
    where = f"{name}.forward"
    if not isinstance(fw, dict):
        return p + [f"{where}: an object"]
    extra = set(fw) - FORWARD_KEYS
    if extra:
        return p + [f"{where}: unknown key {sorted(extra)[0]!r}"]
    p += [f"{where}: needs {k!r}" for k in FORWARD_NEEDED if k not in fw]
    if "start" not in arm:
        p.append(f"{where}: a forward rule reads from the start date, and there is none")
    if "measure" in fw and fw["measure"] not in MEASURES:
        p.append(f"{where}.measure: one of {list(MEASURES)}")
    if "min_obs" in fw and not (
        isinstance(fw["min_obs"], int) and not isinstance(fw["min_obs"], bool) and fw["min_obs"] >= 1
    ):
        p.append(f"{where}.min_obs: a whole number of at least 1")
    for k in ("kill_z", "drift_z"):
        if k in fw and not (_num(fw[k]) and fw[k] < 0):
            p.append(f"{where}.{k}: a negative number")
    if _num(fw.get("kill_z")) and _num(fw.get("drift_z")) and not fw["kill_z"] < fw["drift_z"]:
        p.append(f"{where}.kill_z: below drift_z")
    for k in ("field", "per"):
        if k in fw and not (isinstance(fw[k], str) and fw[k]):
            p.append(f"{where}.{k}: a ledger field name")
    if "beside" in fw and not (
        isinstance(fw["beside"], list) and all(isinstance(x, str) for x in fw["beside"])
    ):
        p.append(f"{where}.beside: a list of automation names")
    return p


SUGGEST_DAYS = 365


def _expiry_class(arm: dict) -> str:
    e = arm["expiry"]
    if e.get("kind") in ("dte", "market_days") and e.get("days") in (0, 1) and not e.get("tolerance"):
        return "0dte" if e["days"] == 0 else "1dte"
    if e.get("monthly"):
        return "monthly"  # standard monthlies only, which a monthlies store holds
    return "all"  # a nearer or farther listed expiry: a weekly or a monthly, which only `all` holds both of


CLOSE_TICK = 15 * 60 + 59  # the end-of-day chains' one tick on a full session (`DailyChainHistory.tick`)


def _cadence(arm: dict) -> str:
    """1d when the end-of-day chains can price the entry: a daily trigger, or a time entry whose window
    holds the 15:59 tick by the engine's own test (`entry_due`); else 1m."""
    e = arm["entry"]
    daily = e.get("kind") == "trigger" and (e.get("trigger") or {}).get("daily")
    at_close = False
    if e.get("kind") == "time":
        at = hhmm(e["at"])
        at_close = at <= CLOSE_TICK < at + e.get("window", 5)
    return "1d" if daily or at_close else "1m"


#: Why no store can serve a chain the driver cannot replay: an entry inside the session on an expiry
#: after it, outside the full set's intraday stores (the end-of-day chains price only the close).
NO_REPLAY = (
    "the backtest replays a later expiry through the session only from the full set's stores (its seven "
    "underlyings, out to 90 days), elsewhere only at the close (15:59); an entry inside the session needs "
    "the intraday replay"
)


def replays(need: dict, source: src.Source) -> bool:
    """Whether the automation driver can replay `need` from `source` as one store. A chain (and its
    greeks) on a same-day expiry comes from a store in the recorder's schema (`history.StoreHistory`),
    any other expiry from end-of-day chains at the 15:59 tick (`history.DailyChainHistory`). Other
    fields are read as they are. The full set's intraday replay reads several stores together, so it
    is asked separately (`intraday_cover`)."""
    if need["field"] not in ("chain", "greeks"):
        return True
    if need.get("expiry") == "0dte":
        return "0dte" in source.expiries and source.probe.backend in ("sqlite", "store_cache")
    return need["cadence"] == "1d" and source.cadence == "1d"


def _reader(need: dict):
    """`replays` for one need, as `coverage`'s `only`."""
    return lambda source: replays(need, source)


#: The full set's stores a multi-day automation is replayed from through the session
#: (`history.IntradayChainHistory`): the near store (1 to `NEAR_DAYS` calendar days out, one minute),
#: the middle store (to `MID_DAYS`, five minutes) and the same-day store for an expiry's own day.
NEAR_DAYS, MID_DAYS = 7, 90
#: The replay a need gets from them, as a suggestion row and a window name it.
INTRADAY = "intraday"


def entry_days(arm: dict) -> int | None:
    """The most calendar days out the expiry rule can enter, or None when the rule has no bound. A
    market-day count is read at seven calendar days a five, plus a holiday; `at_least` reaches the next
    weekly past its floor, or the next monthly when it takes monthlies only."""
    e = arm["expiry"]
    kind = e.get("kind")
    if kind == "between":
        return int(e["max"])
    if kind == "at_least":
        return int(e["days"]) + (35 if e.get("monthly") else 7)
    if kind == "market_days":
        return -(-int(e["days"]) * 7 // 5) + 1
    if kind in ("dte", "calendar"):
        return int(e["days"]) + int(e.get("tolerance") or 0)
    return None


def intraday_parts(arm: dict) -> list[str] | None:
    """The registry names of the full-set stores an intraday replay of `arm` reads, the farthest
    first: the middle store when it can enter past `NEAR_DAYS`, the near store, and the same-day store.
    None for a same-day automation, one that can enter past `MID_DAYS`, or an underlying outside the
    set."""
    days = entry_days(arm)
    if _expiry_class(arm) == "0dte" or days is None or days > MID_DAYS:
        return None
    sym = arm["symbol"].lower()
    parts = [f"vendor-near-{sym}", f"vendor-0dte-{sym}-60s-full"]
    if days > NEAR_DAYS:
        parts.insert(0, f"vendor-mid-{sym}")
    known = {s.name for s in src.SOURCES}
    return parts if all(p in known for p in parts) else None


def intraday_cover(arm: dict, inventory: dict, start: str, end: str, coverage) -> dict | None:
    """Whether the full set's stores cover an intraday replay of `arm` over `start`..`end`: each part
    asked on its own (`coverage`, licensed). {"covered", "source" (the farthest part), "parts",
    "through", "density", "nearest"?: the first part short of it, with why}. None when no part applies
    (`intraday_parts`)."""
    parts = intraday_parts(arm)
    if not parts:
        return None
    out = {"covered": True, "source": parts[0], "parts": parts, "through": end, "density": None}
    for name in parts:
        s = src.by_name(name)
        got = coverage(inventory, "chain", arm["symbol"], start, end, cadence=s.cadence, expiry=s.expiries[0],
                       licensed=True, only=lambda x, n=name: x.name == n, min_density=0.0)  # fmt: skip
        row = next((r for r in got.get("sources") or [] if r.get("source") == name), {})
        if not got.get("covered"):
            out["covered"] = False
            out.setdefault("nearest", {k: row.get(k) for k in ("source", "first", "last", "why")})
            continue
        out["through"] = min(out["through"], row.get("through") or end)
        if row.get("density") is not None:
            out["density"] = row["density"] if out["density"] is None else min(out["density"], row["density"])
    return out


def intraday_span(arm: dict, inventory: dict, last: str, coverage) -> tuple[str, str] | None:
    """The span every part of an intraday replay holds (the latest first, the earliest last), or None."""
    parts = intraday_parts(arm)
    if not parts:
        return None
    firsts, lasts = [], []
    for name in parts:
        s = src.by_name(name)
        got = coverage(inventory, "chain", arm["symbol"], EARLIEST, last, cadence=s.cadence,
                       expiry=s.expiries[0], licensed=True, only=lambda x, n=name: x.name == n,
                       min_density=0.0)  # fmt: skip
        row = next((r for r in got.get("sources") or [] if r.get("source") == name), {})
        if not row.get("first"):
            return None
        firsts.append(row["first"])
        lasts.append(min(row.get("last") or last, last))
    lo, hi = max(firsts), min(lasts)
    return (lo, hi) if lo <= hi else None


def needs(arm: dict) -> list[dict]:
    """What a backtest of `arm` reads: [{"field", "expiry"?, "cadence"}]."""
    expiry, cadence = _expiry_class(arm), _cadence(arm)
    out = [{"field": "chain", "expiry": expiry, "cadence": cadence}]
    if any((leg.get("strike") or {}).get("rule") == "delta" for leg in arm["legs"]):
        out.append({"field": "greeks", "expiry": expiry, "cadence": cadence})
    e = arm["entry"]
    intraday_trigger = e.get("kind") == "orb" or (
        e.get("kind") == "trigger" and not (e.get("trigger") or {}).get("daily")
    )
    if intraday_trigger:
        out.append({"field": "spot", "cadence": "1m"})
    return out


def suggestion(
    arm: dict, inventory: dict, today: date, coverage, *, licensed_ok: bool = False
) -> dict | None:
    """None when every block is backtested; else {"blocks", "window", "needs": [{..., "covered",
    "source", "licensed"}], "possible", "licensed"}. `coverage` is `core.sources.coverage` (passed in,
    so this stays pure). The window ends at the last finished session, or where the covering stores
    stop when one is a few sessions behind (`sources.LAG`). A need for every expiry that our own stores
    miss but would cover for monthlies only says so (`monthly`). Our own stores are asked first for
    each need; only when they fall short and `licensed_ok` (the licence allows new work) is a licensed
    store asked, and a need it covers is marked `licensed`. The suggestion is `licensed` when any need
    is, so its result will be too."""
    lab = labels(arm)
    open_blocks = [b for b, r in lab.items() if r["state"] != BACKTESTED]
    if not open_blocks:
        return None
    last = cal.previous_trading_day(today)  # the last finished session; today's is not in a store yet
    end = last.isoformat()
    start = date.fromordinal(last.toordinal() - SUGGEST_DAYS).isoformat()
    rows = []
    # A multi-day automation our own chains fall short for is replayed through the session from the
    # full set's stores where they cover (every bar, not only the close), the vendor's end-of-day
    # chains otherwise.
    intra = intraday_cover(arm, inventory, start, end, coverage) if licensed_ok else None
    for need in needs(arm):

        def ask(licensed: bool, need=need) -> dict:
            return coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                            expiry=need.get("expiry"), licensed=licensed,
                            only=_reader(need))  # fmt: skip

        got, licensed = ask(False), False
        multi_day = need["field"] in CHAIN_FIELDS and need.get("expiry") != "0dte"
        if not got.get("covered") and multi_day and intra and intra["covered"]:
            rows.append({**need, "covered": True, "source": intra["source"], "licensed": True,
                         "replay": INTRADAY, "through": intra["through"]})  # fmt: skip
            continue
        if not got.get("covered") and licensed_ok:
            vendor = ask(True)
            if vendor.get("covered"):
                got, licensed = vendor, True
        row = {**need, "covered": bool(got.get("covered")), "source": got.get("best"), "licensed": licensed}
        if row["covered"]:
            best = next((s for s in got.get("sources") or [] if s.get("source") == got["best"]), {})
            row["through"] = best.get("through") or end
        else:
            # The nearest our stores come: the first source holding anything, with its span and why it
            # falls short (own recordings that began recently, a source without the expiry).
            near = next((s for s in got.get("sources") or [] if s.get("first")), None)
            if multi_day and intra and intra.get("nearest") and (near is None or need["cadence"] != "1d"):
                # The full set holds the underlying: its store that falls short, rather than no replay.
                row["nearest"] = intra["nearest"]
            elif near is not None:
                row["nearest"] = {k: near.get(k) for k in ("source", "first", "last", "why")}
            elif (
                need["field"] in ("chain", "greeks")
                and need.get("expiry") != "0dte"
                and need["cadence"] != "1d"
            ):
                row["why"] = NO_REPLAY
            elif not got.get("sources"):
                row["why"] = (
                    f"none of our stores holds {need.get('expiry') or 'its'} expiries at {need['cadence']}"
                )
            if need.get("expiry") == "all":
                # Every expiry is asked for since the rule can land on a weekly; monthlies may be held.
                alt = coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                               expiry="monthly", licensed=False,
                               only=_reader({**need, "expiry": "monthly"}))  # fmt: skip
                if alt.get("covered"):
                    held = next((s for s in alt.get("sources") or [] if s.get("source") == alt["best"]), {})
                    row["monthly"] = {
                        "source": alt["best"],
                        "first": held.get("first"),
                        "through": held.get("through"),
                    }
        rows.append(row)
    possible = all(r["covered"] for r in rows)
    if possible and rows:
        end = min(r["through"] for r in rows)  # a store a session or two behind: the run ends where it does
    return {
        "blocks": open_blocks,
        "window": [start, end],
        "needs": rows,
        "possible": possible,
        "licensed": any(r["licensed"] for r in rows),
        "windows": windows(arm, inventory, today, coverage, licensed_ok=licensed_ok),
    }


#: No window starts before this: the chain stores reach no further back.
EARLIEST = "2010-01-04"
#: A store holding less of its span's sessions than this is not offered (a short sample store); Dolt's
#: thin early years (about three days a week) are above it and offered as thin.
OFFER_DENSITY = 0.5
CHAIN_FIELDS = ("chain", "greeks")


def _window(arm: dict, inventory: dict, start: str, end: str, coverage, source: src.Source) -> dict | None:
    """A run over `start`..`end` with the chain (and its greeks) from `source`, every other need from a
    store as `suggestion` asks one (our own, the vendor's only alongside a licensed chain). None unless
    every need is covered. The chain is taken at any density and the density is reported, so a thin
    span (Dolt before 2025) is a choice made knowingly, never a refusal."""
    out = {"start": start, "end": end, "source": source.name, "licensed": source.licensed, "density": None}
    for need in needs(arm):
        pinned = need["field"] in CHAIN_FIELDS
        only = (lambda s, n=need: s.name == source.name and replays(n, s)) if pinned else _reader(need)
        got = coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                       expiry=need.get("expiry"), licensed=source.licensed if pinned else False,
                       only=only, min_density=0.0 if pinned else src.DENSE)  # fmt: skip
        if not got.get("covered") and not pinned and source.licensed:
            got = coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                           expiry=need.get("expiry"), licensed=True, only=only)  # fmt: skip
        if not got.get("covered"):
            return None
        row = next((s for s in got.get("sources") or [] if s.get("source") == got.get("best")), {})
        out["end"] = min(out["end"], row.get("through") or end)
        if pinned and row.get("density") is not None:
            out["density"] = row["density"] if out["density"] is None else min(out["density"], row["density"])
    out["thin"] = out["density"] is not None and out["density"] < src.DENSE
    out["holds"] = source.holds
    out["key"] = f"{source.name}:{out['start']}:{out['end']}"
    return out


def _intraday_window(arm: dict, inventory: dict, start: str, end: str, coverage) -> dict | None:
    """`_window` for an intraday replay from the full set's stores (`intraday_cover`): the chain from
    them, every other need as `_window` asks it. None unless every need is covered."""
    cov = intraday_cover(arm, inventory, start, end, coverage)
    if not cov or not cov["covered"]:
        return None
    out = {"start": start, "end": min(end, cov["through"]), "source": cov["source"], "licensed": True,
           "density": cov["density"], "replay": INTRADAY}  # fmt: skip
    for need in needs(arm):
        if need["field"] in CHAIN_FIELDS:
            continue
        for licensed in (False, True):
            got = coverage(inventory, need["field"], arm["symbol"], start, end, cadence=need["cadence"],
                           expiry=need.get("expiry"), licensed=licensed, only=_reader(need),
                           min_density=src.DENSE)  # fmt: skip
            if got.get("covered"):
                break
        if not got.get("covered"):
            return None
        row = next((s for s in got.get("sources") or [] if s.get("source") == got.get("best")), {})
        out["end"] = min(out["end"], row.get("through") or end)
    out["thin"] = out["density"] is not None and out["density"] < src.DENSE
    out["holds"] = f"{src.by_name(cov['source']).holds}, replayed through the session with its nearer stores"
    out["key"] = f"{INTRADAY}:{cov['source']}:{out['start']}:{out['end']}"
    return out


def windows(arm: dict, inventory: dict, today: date, coverage, *, licensed_ok: bool = False) -> list[dict]:
    """The windows a Builder run may take, from what the stores hold, our own first: for each store the
    driver can replay the chain from, the last year it holds and everything it holds, each ending where
    the store stops (a store that stopped early still offers its span). A licensed store is offered
    only while the licence allows new work, and only for a window that starts before every one of our
    own. A store holding under `OFFER_DENSITY` of its span is left out, and of two stores with the same
    span the first (finest cadence) is kept; a window inside one already offered (as licensed or not)
    is left out. Each is {"key", "start", "end", "source", "licensed", "density", "thin", "holds"};
    `key` is what a run names. Pure over `coverage` and the registry."""
    last = cal.previous_trading_day(today).isoformat()
    year = date.fromordinal(cal.previous_trading_day(today).toordinal() - SUGGEST_DAYS).isoformat()
    chain = next(n for n in needs(arm) if n["field"] == "chain")
    out: list[dict] = []
    for licensed in (False, True) if licensed_ok else (False,):
        spans = coverage(inventory, "chain", arm["symbol"], EARLIEST, last, cadence=chain["cadence"],
                         expiry=chain.get("expiry"), licensed=licensed, only=_reader(chain),
                         min_density=0.0)  # fmt: skip
        for row in spans.get("sources") or []:
            if not row.get("first") or row.get("licensed", False) != licensed:
                continue
            try:
                source = src.by_name(row["source"])
            except KeyError:
                continue
            first, stop = max(row["first"], EARLIEST), min(row.get("last") or last, last)
            if first > stop:
                continue
            earliest_own = min((w["start"] for w in out if not w["licensed"]), default=None)
            for lo in dict.fromkeys((max(first, year), first)):
                if licensed and earliest_own is not None and lo >= earliest_own:
                    continue  # the vendor's only where it reaches further back than our own
                w = _window(arm, inventory, lo, stop, coverage, source)
                if w is None or (w["density"] is not None and w["density"] < OFFER_DENSITY):
                    continue
                inside = any(
                    x["licensed"] == w["licensed"] and x["start"] <= w["start"] and w["end"] <= x["end"]
                    for x in out
                )
                if not inside:
                    out.append(w)
    span = intraday_span(arm, inventory, last, coverage) if licensed_ok else None
    if span:
        # The full set's intraday replay is offered beside the end-of-day one: a finer read of the same
        # automation, so a window inside an end-of-day one is still offered.
        first, stop = max(span[0], EARLIEST), span[1]
        for lo in dict.fromkeys((max(first, year), first)):
            w = _intraday_window(arm, inventory, lo, stop, coverage)
            if w is None or (w["density"] is not None and w["density"] < OFFER_DENSITY):
                continue
            if not any(
                x.get("replay") == INTRADAY and x["start"] <= w["start"] and w["end"] <= x["end"] for x in out
            ):
                out.append(w)
    return out


def builder_view(arm: dict, suggestion_row: dict | None) -> dict:
    """One automation as the Builder page shows it: its identity, each block with its keys and label,
    its start and forward rule, and the backtest suggestion when it has one."""
    lab = labels(arm)
    reg = arm.get("register") or {}
    return {
        "name": arm["name"],
        "group": arm.get("group"),
        "symbol": arm["symbol"],
        "enabled": bool(arm.get("enabled", True)),
        "note": arm.get("note"),
        "start": arm.get("start"),
        "register": {"item": reg["item"], "verdict": reg["verdict"]} if reg else None,
        "summary": summary(arm),
        "blocks": [
            {"block": b, "state": lab[b]["state"], "keys": {k: arm[k] for k in keys if k in arm}}
            for b, keys in BLOCKS.items()
        ],
        "forward": arm.get("forward"),
        "suggestion": suggestion_row,
    }


def joins(arm: dict, today: date) -> bool:
    """Whether the paper loop may enter `arm` on `today`: on or after its start, when it has one."""
    return "start" not in arm or today.isoformat() >= arm["start"]


def forward_rows(arms: dict[str, dict]) -> list[dict]:
    """Each enrolled automation's forward check as a register-shaped row: {"id", "study", "forward"}.
    The orchestrator's `forward-checks` reads these beside the register's own."""
    out = []
    for name, arm in sorted(arms.items()):
        fw = arm.get("forward")
        if not fw or "start" not in arm:
            continue
        block = {
            "module": MODULE,
            "schema": SCHEMA,
            "arm": name,
            "since": arm["start"],
            "reference": None,
            **fw,
        }
        out.append({"id": f"automation:{name}", "study": f"{name}, forward test", "forward": block})
    return out
