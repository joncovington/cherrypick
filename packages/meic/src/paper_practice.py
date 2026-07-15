"""0DTESPX practice-session backtester for the MEIC paper engine (Phase 1: client + one-profile day).

Drives our REAL entry logic (paper.evaluate_entry, unchanged) through 0DTESPX.com *practice
sessions* — the platform's server-side replay sandbox — instead of extracting their historical
dataset. This is the ToS-COMPLIANT counterpart to src/paper_replay.py (which is disabled: its
bulk-extraction design violates 0DTESPX acceptable use). See docs/paper-practice-plan.md and
docs/0dtespx-api.md.

Compliance posture (enforced by construction):
  - The option chain is read ONLY to make an entry decision at that tick, then discarded — never
    persisted. Stop management reads FREE position marks, not chains. Only our own trades/results
    are ever stored. This is trading/research use, not systematically walking the archive to build
    a local copy or derivative dataset.

Phase 2 scope: all SPX-eligible profiles (the four ladder tiers + large-spx + explore-spx-tightcredit)
in one run, each its own practice session for position isolation, sharing ONE metered chain snapshot
per tick so credit cost is per-tick not per-profile. Per-IC results are written to a practice DB
(ic_trades schema, execution_mode='practice_0dtespx') so get_range_summary / the dashboard / the EOD
roll-up work unchanged. Hardened in Phase 1 (fill confirmation via position reconciliation,
idempotency keys, 429 backoff). Phase 3 resolves the iv_rank gate with a ToS-safe VIX-band pseudo
rank (vix_band_iv_rank — no historical reads; see _VIX_IV_RANK_BANDS). Phase 4 adds multi-day batching
(run_range) with rate-limit pacing and a range roll-up. Phase 5 investigated fee alignment and found
0DTESPX's SPX fee schedule already matches cherrypick.core.fees to the cent, so results (tagged
cost_basis='0dtespx') are fee-comparable to tastytrade forward paper with no alignment; the only
residual is 0DTESPX's 0.05 slippage, baked into fills. We deliberately do NOT mutate the account's
fee settings (PATCH /user would, but globally). SPX-only.

CLI:
  python src/paper_practice.py run --date 2026-07-09 [--profiles a,b | --profile large-spx]
                                   [--cadence 120] [--dry] [--db <path>]
  python src/paper_practice.py run --start 2026-07-01 --end 2026-07-10   # paced multi-day batch
  python src/paper_practice.py report --start 2026-07-01 --end 2026-07-10 # per-profile roll-up
  python src/paper_practice.py status         # rate-limit bucket fill (usage_percent)
  python src/paper_practice.py login --email <you>   # store a token (delegates to paper_replay)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper  # noqa: E402  (reused: evaluate_entry, profiles, merge, settlement value)
import paper_replay as _replay  # noqa: E402  (reused: _API_BASE, _USER_AGENT, _token, login helpers)
import paths as _paths  # noqa: E402  (practice_trades.db default path in the data home)

_DB_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.py")

_ET = ZoneInfo("America/New_York")
_TARGET_DELTA = 0.15
# VIX-band pseudo iv_rank for practice runs (Phase 3; ToS-safe — no historical reads). A true
# iv_rank is a multi-day VIX percentile, which would mean systematically walking past sessions to
# seed it; instead each tick's live VIX maps to a monotonic rank via these documented bands
# (reasoned starting points, tunable — not backtested-optimal). Feeding the existing iv_rank gate
# keeps per-profile min_iv_rank floors and the low-IV credit relief working; rows are tagged
# iv_rank_source="vix_band" so they stay distinct from forward-paper's native rank.
_VIX_IV_RANK_BANDS = [(12, 0.10), (15, 0.25), (18, 0.40), (22, 0.55), (27, 0.70), (35, 0.85)]


# ---------------------------------------------------------------------------
# 0DTESPX client (hardened: idempotency + 429 backoff)
# ---------------------------------------------------------------------------

def _parse_retry_after(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 5.0


class Client:
    """Thin authenticated 0DTESPX API client. Auth reuses the keyring token + Cloudflare
    User-Agent stored by `paper_replay login`. Injectable so the driver can be unit-tested
    against a canned stand-in with no network."""

    def __init__(self, base=None, ua=None, token_fn=None, timeout=25, max_retries=4):
        self.base = base or _replay._API_BASE
        self.ua = ua or _replay._USER_AGENT
        self.token_fn = token_fn or _replay._token
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method, path, body=None, idem=None):
        for attempt in range(self.max_retries + 1):
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Authorization": self.token_fn(), "User-Agent": self.ua}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if idem:
                headers["Idempotency-Key"] = idem
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode()
                    return r.status, (json.loads(raw) if raw else None)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < self.max_retries:
                    time.sleep(min(_parse_retry_after(exc.headers.get("Retry-After")), 30))
                    continue
                return exc.code, exc.read().decode("utf-8", "replace")[:300]
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(2)
                    continue
                return 0, str(exc.reason)

    # --- session lifecycle (free/unmetered) ---
    def open_practice(self, date):
        _, d = self._request("POST", "/practice/sessions", {"date": date})
        return d["id"] if isinstance(d, dict) else None

    def set_clock(self, sid, utc_iso_z):
        return self._request("PATCH", f"/practice/sessions/{sid}", {"time": utc_iso_z})

    def positions(self, sid):
        return self._request("GET", f"/practice/sessions/{sid}/positions")[1]

    def session(self, sid):
        return self._request("GET", f"/practice/sessions/{sid}")[1]

    def transactions(self, sid):
        return self._request("GET", f"/practice/sessions/{sid}/transactions")[1]

    def place(self, sid, order):
        return self._request("POST", f"/practice/sessions/{sid}/orders", order, idem=uuid.uuid4().hex)

    # --- market data ---
    def snapshot(self, ts):  # METERED (10 credits) — entry decisions only
        return self._request("GET", f"/market-data/option-chain-snapshots/{ts}")[1]

    def historical(self, date, series):  # METERED (10 credits) — once per day (spot/vix lookup)
        return self._request("GET", f"/market-data/historical/{date}?series={series}")[1]

    def usage_percent(self):
        _, u = self._request("GET", "/user")
        return u.get("usage_percent") if isinstance(u, dict) else None

    def available_sessions(self):
        """Set of dates 0DTESPX has data for (GET /market-data/sessions, public)."""
        _, d = self._request("GET", "/market-data/sessions")
        return set(d.keys()) if isinstance(d, dict) else set()


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no network)
# ---------------------------------------------------------------------------

def occ(strike, cp, yymmdd) -> str:
    """OCC/OSI instrument string for an SPX weekly (0DTE) option, e.g.
    occ(7450, 'P', '260709') -> 'SPXW  260709P07450000' (6-char root 'SPXW' + 2 spaces)."""
    return f"SPXW  {yymmdd}{cp}{int(round(strike)) * 1000:08d}"


def yymmdd_of(date: str) -> str:
    return datetime.strptime(date, "%Y-%m-%d").strftime("%y%m%d")


def vix_band_iv_rank(vix):
    """Map a tick's VIX onto a pseudo iv_rank via _VIX_IV_RANK_BANDS (see that constant). Practice
    runs use this instead of a true multi-day VIX percentile, which the 0DTESPX ToS won't let us
    bulk-seed. Returns a neutral 0.40 when VIX is unavailable this tick."""
    if vix is None:
        return 0.40
    for ceiling, rank in _VIX_IV_RANK_BANDS:
        if vix <= ceiling:
            return rank
    return 0.95


def session_quality(now_min: int) -> str:
    if now_min < 615:
        return "open_volatile"
    if now_min < 720:
        return "prime"
    if now_min < 840:
        return "midday"
    if now_min < 885:
        return "afternoon"
    return "late"


def build_candidates(snap: dict, spot: float, widths, target_delta: float, yymmdd: str) -> list:
    """Turn a 0DTESPX option-chain snapshot (flat call_<k>/put_<k> -> {bid,ask,delta}) into the
    candidate shape paper.evaluate_entry expects: nearest-delta short strikes with wings at each
    width. streamer_symbol is the OCC instrument, so the chosen candidate maps straight to an order."""
    calls, puts = {}, {}
    for k, v in (snap or {}).items():
        if not (v and isinstance(v, dict)):
            continue
        try:
            strike = int(k.split("_")[1])
        except (IndexError, ValueError):
            continue
        (calls if k.startswith("call_") else puts)[strike] = v

    def near(pool):
        cand = {s: d for s, d in pool.items() if d.get("delta") is not None}
        return min(cand, key=lambda s: abs(abs(cand[s]["delta"]) - target_delta)) if cand else None

    sc = near({s: d for s, d in calls.items() if s > spot})
    sp = near({s: d for s, d in puts.items() if s < spot})
    if sc is None or sp is None:
        return []

    def leg(strike, d, cp):
        return {"strike": float(strike), "streamer_symbol": occ(strike, cp, yymmdd),
                "delta": d["delta"], "bid": d["bid"], "ask": d["ask"]}

    cands = []
    for w in sorted(widths):
        lc, lp = sc + w, sp - w
        if lc in calls and lp in puts:
            cands.append({"wing_width": w, "short_put": leg(sp, puts[sp], "P"),
                          "long_put": leg(lp, puts[lp], "P"), "short_call": leg(sc, calls[sc], "C"),
                          "long_call": leg(lc, calls[lc], "C")})
    return cands


def mark_map(positions) -> dict:
    """{instrument: mark_price} for the currently-OPEN legs from a free positions read — the
    per-side stop input. A closed leg is still listed by 0DTESPX with quantity "0" /
    direction "zero"; those are excluded so reconcile() sees the side as gone (this is what fill
    confirmation keys off — a leg absent from this map has been closed)."""
    if not isinstance(positions, list):
        return {}
    out = {}
    for p in positions:
        inst, price, qty = p.get("instrument"), p.get("price"), p.get("quantity")
        if inst is None or price is None:
            continue
        try:
            if abs(float(qty)) < 1e-9:   # closed leg still listed with quantity 0 -> treat as gone
                continue
        except (TypeError, ValueError):
            pass  # missing/invalid quantity -> keep (defensive)
        out[inst] = float(price)
    return out


def _sides(ic):
    L = ic["legs"]
    return {"call": (L["sc"], L["lc"]), "put": (L["sp"], L["lp"])}


def reconcile(ic, marks) -> None:
    """Fill confirmation by position reconciliation: a side whose BOTH legs have left the book is
    confirmed closed (a filled stop/expiry removes them). This is the source of truth rather than
    trusting an order response's fill_price (the prototype saw that come back null)."""
    for side, (short_i, long_i) in _sides(ic).items():
        if ic[side] in ("open", "pending_close") and short_i not in marks and long_i not in marks:
            ic[side] = "closed"


def side_cost(ic, side, marks):
    """Cost to close one side = short mark − long mark, or None if either leg isn't marked."""
    short_i, long_i = _sides(ic)[side]
    if short_i in marks and long_i in marks:
        return marks[short_i] - marks[long_i]
    return None


def spx_eligible_profiles(base=None, profiles=None) -> list:
    """The profiles this SPX-only backtester runs: every profile whose merged config trades SPX —
    the four ladder tiers (they trade all base symbols, SPX included) plus the SPX-pinned experiment
    cells (large-spx, explore-spx-tightcredit). XSP/QQQ/IWM-pinned cells are excluded."""
    base = base or paper.load_base_config()
    profiles = profiles or paper.load_profiles()
    out = []
    for name in paper.all_profile_names(profiles):
        params = paper._merged_params(base, profiles[name])
        if "SPX" in [s.upper() for s in params.get("symbols", ["SPX"])]:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Results DB (per-IC rows via db.py, tagged execution_mode="practice_0dtespx")
# ---------------------------------------------------------------------------

def _db(db_path, args):
    subprocess.run([sys.executable, _DB_PY, "--db", db_path] + args, capture_output=True, text=True)


def _db_json(db_path, args):
    r = subprocess.run([sys.executable, _DB_PY, "--db", db_path] + args, capture_output=True, text=True)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None


def _save_ic(db_path, row):
    _db(db_path, ["save_trade", "--data", json.dumps(row, default=str)])


def _range_summary(db_path, start, end):
    d = _db_json(db_path, ["get_range_summary", "--start", start, "--end", end])
    return d.get("profiles") if isinstance(d, dict) else None


def _days_in_range(start, end, available):
    """Sorted YYYY-MM-DD dates in [start, end] that 0DTESPX actually has sessions for (lexical
    compare is valid for zero-padded ISO dates). Pure — unit-tested without network."""
    return sorted(d for d in available if start <= d <= end)


class _Book:
    """One profile's isolated virtual account = its own 0DTESPX practice session (positions can't
    commingle across profiles, so each profile gets its own session; the metered chain snapshot is
    still read once per tick and shared across all books)."""

    def __init__(self, name, params, sid):
        self.name = name
        self.params = params
        self.sid = sid
        self.widths = params.get("wing_widths_by_symbol", {}).get("SPX") or [5, 10]
        self.stop_trig = params["stop_trigger_ratio"]
        self.max_adj = params.get("max_stop_adjustments_per_ic", 3)
        self.max_conc = params["max_concurrent_ics"]
        self.daily_target = params.get("daily_ic_trade_target", 10 ** 9)
        self.spacing = params.get("min_minutes_between_entries", 0)
        self.open_ics = []
        self.todays = 0
        self.last_min = None
        self.seq = 0

    def account_open(self):
        return sum(1 for ic in self.open_ics if ic["call"] == "open" or ic["put"] == "open")

    def eligible(self, now_min):
        return (600 <= now_min < 870
                and self.account_open() < self.max_conc
                and self.todays < self.daily_target
                and (self.last_min is None or now_min - self.last_min >= self.spacing))


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def _place_ic(client, sid, chosen, yymmdd, now_min, entry_iso, spot):
    L = {"sp": occ(chosen["short_put"]["strike"], "P", yymmdd),
         "lp": occ(chosen["long_put"]["strike"], "P", yymmdd),
         "sc": occ(chosen["short_call"]["strike"], "C", yymmdd),
         "lc": occ(chosen["long_call"]["strike"], "C", yymmdd),
         "sp_k": chosen["short_put"]["strike"], "sc_k": chosen["short_call"]["strike"]}
    order = {"type": "limit", "price": f"{round(chosen['ic_natural_bid'], 2):.2f}",
             "price_effect": "credit",
             "legs": [{"instrument": L["sp"], "quantity": "1", "action": "sell to open"},
                      {"instrument": L["lp"], "quantity": "1", "action": "buy to open"},
                      {"instrument": L["sc"], "quantity": "1", "action": "sell to open"},
                      {"instrument": L["lc"], "quantity": "1", "action": "buy to open"}]}
    _, resp = client.place(sid, order)
    if not isinstance(resp, dict):
        return None
    fill = resp.get("fill_price") or resp.get("execution_price")
    if fill is None:
        return None
    return {"legs": L, "net_credit": float(fill), "open_fee": float(resp.get("fees") or 0),
            "entry_min": now_min, "entry_iso": entry_iso, "spot_entry": spot,
            "wing": chosen["wing_width"], "call": "open", "put": "open",
            "retry": {"call": 0, "put": 0},
            # per-side exit debit (points) paid to close a stopped side; filled at settlement for the
            # sides left to expire. drives per-IC P&L: net_credit - call_exit - put_exit.
            "exit": {"call": None, "put": None}, "exit_fee": {"call": 0.0, "put": 0.0},
            "exit_reason": {"call": None, "put": None}}


def _place_close(client, sid, ic, side, cost, log, tag, retry=False):
    short_i, long_i = _sides(ic)[side]
    # A defined-risk spread never costs more than its wing to close, so bid the wing width as the
    # debit limit: always marketable (the sim fills at the true, better price via improvement),
    # which avoids the mid-priced limit sitting unfilled when the spread is deep ITM.
    price = max(round(float(ic["wing"]), 2), 0.05)
    order = {"type": "limit", "price": f"{price:.2f}", "price_effect": "debit",
             "legs": [{"instrument": short_i, "quantity": "1", "action": "buy to close"},
                      {"instrument": long_i, "quantity": "1", "action": "sell to close"}]}
    st, resp = client.place(sid, order)
    ic[side] = "pending_close"
    fp = resp.get("fill_price") if isinstance(resp, dict) else None
    fee = resp.get("fees") if isinstance(resp, dict) else None
    if fp is not None and ic["exit"][side] is None:   # record the realized close debit once
        ic["exit"][side] = float(fp)
        ic["exit_fee"][side] = float(fee or 0)
        ic["exit_reason"][side] = "per_side_stop"
    log(f"{tag} {'STOP-retry' if retry else 'STOP'} {side} cost {cost:.2f} bid {price} "
        f"http {st} fill {fp}")


# ---------------------------------------------------------------------------
# Settlement / results row
# ---------------------------------------------------------------------------

def _finalize_ic(ic, spot_close):
    """Close out an IC at settlement: any side not stopped intraday expires and settles at its
    intrinsic value (capped at the wing) via paper._settlement_value. Returns
    (pnl, fees, status, exit_reason). Per-IC P&L = net_credit − call_exit − put_exit (points ×100),
    fees separate — mirrors paper.py's accounting, sourced from real 0DTESPX fills + settlement."""
    for side in ("call", "put"):
        if ic["exit"][side] is None:
            strike = ic["legs"]["sc_k"] if side == "call" else ic["legs"]["sp_k"]
            ic["exit"][side] = paper._settlement_value(strike, spot_close, ic["wing"], side)
            ic["exit_reason"][side] = "expired_settlement"
    pnl = round((ic["net_credit"] - ic["exit"]["call"] - ic["exit"]["put"]) * 100, 2)
    fees = round(ic["open_fee"] + ic["exit_fee"]["call"] + ic["exit_fee"]["put"], 2)
    reasons = [r for r in (ic["exit_reason"]["call"], ic["exit_reason"]["put"]) if r]
    status = "stopped" if "per_side_stop" in reasons else "expired"
    return pnl, fees, status, "+".join(sorted(set(reasons)))


def _ic_row(book_name, date, ic, pnl, fees, status, exit_reason, exit_iso):
    L = ic["legs"]
    return {
        "ic_order_id": ic["oid"], "trade_date": date, "entry_time": ic["entry_iso"],
        "exit_time": exit_iso, "expiration": date, "symbol": "SPX",
        "put_strike": L["sp_k"], "call_strike": L["sc_k"], "wing_width": ic["wing"],
        "net_credit": ic["net_credit"], "quantity": 1,
        "underlying_price_entry": ic["spot_entry"], "iv_rank_at_entry": ic.get("iv_rank"),
        "session_quality": session_quality(ic["entry_min"]),
        "risk_profile": book_name, "execution_mode": "practice_0dtespx",
        "iv_rank_source": ic.get("iv_rank_source", "vix_band"), "pnl": pnl, "fees": fees,
        "status": status, "exit_reason": exit_reason,
    }


# ---------------------------------------------------------------------------
# Multi-profile day driver (all SPX-eligible books, one shared per-tick snapshot)
# ---------------------------------------------------------------------------

def run(date, profile_names=None, cadence=120, dry=False, db_path=None,
        iv_rank=None, client=None, log=print):
    """Backtest one SPX day for every given (or all SPX-eligible) profile at once. Each profile is
    its own practice session (position isolation), but the metered option-chain snapshot is read
    ONCE per tick and shared across all books — so credit cost is per-tick, not per-profile. Writes
    one ic_trades row per IC to db_path (tagged execution_mode='practice_0dtespx')."""
    client = client or Client()
    base = paper.load_base_config()
    profiles = paper.load_profiles()
    names = list(profile_names) if profile_names else spx_eligible_profiles(base, profiles)
    for n in names:
        if n not in profiles:
            raise ValueError(f"unknown profile {n!r}")
        p = paper._merged_params(base, profiles[n])
        if "SPX" not in [s.upper() for s in p.get("symbols", ["SPX"])]:
            raise ValueError(f"{n} does not trade SPX (this backtester is SPX-only)")

    if db_path and not dry:
        _db(db_path, ["init_db"])
    used = client.usage_percent()
    if used is not None:
        log(f"# rate-limit bucket usage: {used}%")

    yymmdd = yymmdd_of(date)
    union_widths = paper.union_widths_for_symbol("SPX", base, profiles)
    books = []
    for n in names:
        sid = client.open_practice(date)
        if not sid:
            raise RuntimeError("could not open practice session (auth/token? run `login`)")
        books.append(_Book(n, paper._merged_params(base, profiles[n]), sid))

    series = client.historical(date, "spx,vix")
    spot_map = {int(r["datetimeUnix"]): float(r["spx"]) for r in series if r.get("spx")}
    vix_map = {int(r["datetimeUnix"]): float(r["vix"]) for r in series if r.get("vix")}
    unix_sorted = sorted(spot_map)

    def lookup(u):
        k = min(unix_sorted, key=lambda x: abs(x - u))
        return spot_map[k], vix_map.get(k)

    y, m, d = (int(x) for x in date.split("-"))
    cur = datetime(y, m, d, 9, 35, tzinfo=_ET)
    end = datetime(y, m, d, 16, 0, tzinfo=_ET)
    snaps = 0

    while cur <= end:
        utc = cur.astimezone(UTC)
        utcZ = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        now_et = cur.strftime("%H:%M")
        now_min = cur.hour * 60 + cur.minute
        spot, vix = lookup(int(utc.timestamp()))

        # advance each book's clock + manage its stops off FREE marks
        for b in books:
            client.set_clock(b.sid, utcZ)
            if dry or not b.open_ics:
                continue
            marks = mark_map(client.positions(b.sid))
            for ic in b.open_ics:
                reconcile(ic, marks)
                for side in ("call", "put"):
                    if ic[side] == "open":
                        cost = side_cost(ic, side, marks)
                        if cost is not None and cost >= b.stop_trig * ic["net_credit"]:
                            _place_close(client, b.sid, ic, side, cost, log, f"{now_et} {b.name}")
                    elif ic[side] == "pending_close":
                        cost = side_cost(ic, side, marks)
                        if cost is not None:
                            ic["retry"][side] += 1
                            if ic["retry"][side] <= b.max_adj:
                                _place_close(client, b.sid, ic, side, cost, log,
                                             f"{now_et} {b.name}", retry=True)
                            else:
                                log(f"{now_et} {b.name} WARN {side} close unconfirmed")

        # entry — ONE shared metered snapshot serves every eligible book this tick
        elig = [b for b in books if b.eligible(now_min)]
        if elig:
            snap = client.snapshot(utc.strftime("%Y-%m-%dT%H:%M:%S"))
            snaps += 1
            cands = build_candidates(snap, spot, union_widths, _TARGET_DELTA, yymmdd)
            # VIX-band pseudo iv_rank (ToS-safe) unless an explicit override was passed.
            rank = iv_rank if iv_rank is not None else vix_band_iv_rank(vix)
            rank_source = "override" if iv_rank is not None else "vix_band"
            for b in elig:
                view = {"symbol": "SPX", "date": date, "now_et": now_et, "dte": 0,
                        "underlying_price": spot, "iv_rank": rank, "vix": vix,
                        "vix1d_ratio": None, "atr_5day": None,
                        "session_quality": session_quality(now_min), "gex": {"ok": False},
                        "candidates": cands, "leg_quotes": {}}
                overlap = [{"put_strike": ic["legs"]["sp_k"], "call_strike": ic["legs"]["sc_k"]}
                           for ic in b.open_ics if not (ic["call"] == "closed" and ic["put"] == "closed")]
                entered, reason, chosen = paper.evaluate_entry(
                    view, b.params, overlap, account_open_count=b.account_open(),
                    todays_entry_count=b.todays, last_entry_min=b.last_min)
                if not entered:
                    continue
                if dry:
                    log(f"{now_et} {b.name} DRY would ENTER {chosen['wing_width']}w "
                        f"credit~{chosen['ic_natural_bid']}")
                    b.todays += 1
                    b.last_min = now_min
                    continue
                ic = _place_ic(client, b.sid, chosen, yymmdd, now_min, utcZ, spot)
                if ic:
                    b.seq += 1
                    ic["oid"] = f"PRAC-{b.name}-{b.sid[:8]}-{b.seq}"
                    ic["iv_rank"] = rank
                    ic["iv_rank_source"] = rank_source
                    b.open_ics.append(ic)
                    b.todays += 1
                    b.last_min = now_min
                    log(f"{now_et} {b.name} ENTER {ic['wing']}w "
                        f"sp{ic['legs']['sp_k']:.0f}/sc{ic['legs']['sc_k']:.0f} credit {ic['net_credit']}")
                else:
                    log(f"{now_et} {b.name} ENTER failed (no fill)")
        cur += timedelta(seconds=cadence)

    # settlement — advance to the close, finalize each book's ICs, write rows
    end_utc = end.astimezone(UTC)
    exit_iso = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    spot_close = lookup(int(end_utc.timestamp()))[0]
    result = {"date": date, "dry": dry, "metered_snapshots": snaps + 1,
              "db": db_path if (db_path and not dry) else None, "profiles": {}}
    for b in books:
        client.set_clock(b.sid, exit_iso)
        sess = client.session(b.sid)
        net = 0.0
        for ic in b.open_ics:
            pnl, fees, status, exit_reason = _finalize_ic(ic, spot_close)
            net += pnl - fees
            if db_path and not dry:
                _save_ic(db_path, _ic_row(b.name, date, ic, pnl, fees, status, exit_reason, exit_iso))
        result["profiles"][b.name] = {
            "entries": b.todays, "net_pnl": round(net, 2),
            "session_realized": sess.get("equity_options_realized_profit_loss") if isinstance(sess, dict) else None,
            "session_fees": sess.get("equity_options_fees") if isinstance(sess, dict) else None,
            "sid": b.sid,
        }
    return result


def run_day(date, profile_name="large-spx", cadence=120, dry=False, db_path=None,
            iv_rank=None, client=None, log=print):
    """Single-profile convenience wrapper over run() (back-compat / focused runs)."""
    res = run(date, [profile_name], cadence=cadence, dry=dry, db_path=db_path,
              iv_rank=iv_rank, client=client, log=log)
    return {"date": date, "profile": profile_name, "dry": dry,
            "metered_snapshots": res["metered_snapshots"], **res["profiles"].get(profile_name, {})}


# ---------------------------------------------------------------------------
# Multi-day batch (rate-limit paced)
# ---------------------------------------------------------------------------

# ~credit cost of one day's metered reads (chain snapshots + the once-daily series). Conservative,
# used only to decide when to pause for the leaky bucket to refill (10k cap, ~0.116 credits/s).
_EST_DAY_CREDITS = 1000
_BUCKET_CAP = 10000
_DRAIN_PER_SEC = 0.116


def _pace(client, log, floor=_EST_DAY_CREDITS):
    """Before a day, make sure the rate-limit bucket has ~a day's worth of credits left; if not,
    sleep for it to refill so a mid-day snapshot never 429s and silently drops an entry (which would
    corrupt the backtest rather than the strategy). Bounded per sleep; the caller loops days."""
    used = client.usage_percent()
    if used is None:
        return
    remaining = (100 - used) / 100 * _BUCKET_CAP
    if remaining >= floor:
        return
    wait = min(int((floor - remaining) / _DRAIN_PER_SEC) + 5, 3600)
    log(f"# pacing: bucket {used}% used (~{int(remaining)} cr left); waiting {wait}s to refill")
    time.sleep(wait)


def run_range(start, end, profile_names=None, cadence=120, db_path=None, client=None,
              log=print, pace=True):
    """Backtest every available 0DTESPX session in [start, end] (inclusive) for the given (or all
    SPX-eligible) profiles, accumulating per-IC rows into db_path. Paces against the rate-limit
    bucket between days. Returns per-day net P&L plus a get_range_summary roll-up over the range."""
    client = client or Client()
    days = _days_in_range(start, end, client.available_sessions())
    if not days:
        return {"ok": False, "error": f"no available 0DTESPX sessions in {start}..{end}"}
    per_day = {}
    for day in days:
        if pace:
            _pace(client, log)
        try:
            res = run(day, profile_names, cadence=cadence, dry=False, db_path=db_path,
                      client=client, log=log)
            per_day[day] = {p: s["net_pnl"] for p, s in res["profiles"].items()}
            hits = ", ".join(f"{p} {s['net_pnl']:+.0f}" for p, s in res["profiles"].items() if s["entries"])
            log(f"# {day}: {hits or 'no entries'}")
        except Exception as exc:  # one bad day must not abort the batch
            log(f"# {day}: ERROR {exc}")
            per_day[day] = {"error": str(exc)}
    out = {"start": start, "end": end, "days": len(days), "per_day": per_day,
           "db": db_path, "cost_basis": "0dtespx"}
    if db_path:
        out["range_summary"] = _range_summary(db_path, start, end)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="0DTESPX practice-session MEIC backtester (Phase 4)")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Backtest one SPX day, or a date range, across profiles")
    p_run.add_argument("--date", default=None, help="a single day YYYY-MM-DD")
    p_run.add_argument("--start", default=None, help="range start YYYY-MM-DD (with --end)")
    p_run.add_argument("--end", default=None, help="range end YYYY-MM-DD (with --start)")
    p_run.add_argument("--profiles", default=None,
                       help="comma-separated profiles; default = all SPX-eligible")
    p_run.add_argument("--profile", default=None, help="a single profile (back-compat shortcut)")
    p_run.add_argument("--cadence", type=int, default=120, help="clock step seconds (live loop uses 120)")
    p_run.add_argument("--dry", action="store_true", help="log intended entries without placing orders")
    p_run.add_argument("--db", default=None,
                       help="results DB path (default practice_trades.db in the data home)")

    p_rep = sub.add_parser("report", help="Per-profile P&L roll-up over a date range from the practice DB")
    p_rep.add_argument("--start", required=True)
    p_rep.add_argument("--end", required=True)
    p_rep.add_argument("--db", default=None)

    sub.add_parser("status", help="Print the rate-limit bucket usage_percent")

    p_login = sub.add_parser("login", help="Store a 0DTESPX token (delegates to paper_replay)")
    p_login.add_argument("--email", required=True)
    p_login.add_argument("--code", default=None)

    args = parser.parse_args()

    if args.command == "run":
        names = (args.profiles.split(",") if args.profiles
                 else [args.profile] if args.profile else None)
        db_path = args.db or str(_paths.data_path("practice_trades.db"))
        if args.start and args.end:
            result = run_range(args.start, args.end, names, cadence=args.cadence, db_path=db_path)
        elif args.date:
            result = run(args.date, names, cadence=args.cadence, dry=args.dry, db_path=db_path)
        else:
            print(json.dumps({"ok": False, "error": "provide --date or --start/--end"}))
            sys.exit(1)
        print(json.dumps({"ok": True, "result": result}, default=str))
    elif args.command == "report":
        db_path = args.db or str(_paths.data_path("practice_trades.db"))
        print(json.dumps({"ok": True, "start": args.start, "end": args.end,
                          "profiles": _range_summary(db_path, args.start, args.end)}, default=str))
    elif args.command == "status":
        print(json.dumps({"ok": True, "usage_percent": Client().usage_percent()}))
    elif args.command == "login":
        import getpass
        try:
            pw = None if args.code else getpass.getpass("0DTESPX password: ")
            tok = _replay.login(args.email, password=pw, code=args.code)
        except _replay.ReplayError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            sys.exit(1)
        print(json.dumps({"ok": True, "email": args.email, "stored": _replay._mask(tok)}))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
