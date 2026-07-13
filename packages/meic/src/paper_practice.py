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

Phase 1 scope: a hardened single-profile (default 'large-spx'), single-day driver — the prototype
made production-grade with fill confirmation via position reconciliation, idempotency keys, and 429
backoff. Multi-profile shared-snapshot runs, the results DB, the real iv_rank source, and multi-day
batching are Phases 2–4 (see the plan). SPX-only; runs on 0DTESPX's fee/slippage cost basis.

CLI:
  python src/paper_practice.py run --date 2026-07-09 [--profile large-spx] [--cadence 120] [--dry]
  python src/paper_practice.py status         # rate-limit bucket fill (usage_percent)
  python src/paper_practice.py login --email <you>   # store a token (delegates to paper_replay)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper  # noqa: E402  (reused: evaluate_entry, profiles, merge)
import paper_replay as _replay  # noqa: E402  (reused: _API_BASE, _USER_AGENT, _token, login helpers)

_ET = ZoneInfo("America/New_York")
_TARGET_DELTA = 0.15
# PLACEHOLDER iv_rank (Phase 3 replaces this with a real percentile source — see the plan). A true
# VIX-percentile rank needs multi-day history the per-date endpoint doesn't give; until then every
# practice run passes a fixed rank and tags it so results stay auditable/distinct.
_IV_RANK_PLACEHOLDER = 0.45


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


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no network)
# ---------------------------------------------------------------------------

def occ(strike, cp, yymmdd) -> str:
    """OCC/OSI instrument string for an SPX weekly (0DTE) option, e.g.
    occ(7450, 'P', '260709') -> 'SPXW  260709P07450000' (6-char root 'SPXW' + 2 spaces)."""
    return f"SPXW  {yymmdd}{cp}{int(round(strike)) * 1000:08d}"


def yymmdd_of(date: str) -> str:
    return datetime.strptime(date, "%Y-%m-%d").strftime("%y%m%d")


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


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def _place_ic(client, sid, chosen, yymmdd, now_min, log):
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
    return {"legs": L, "net_credit": float(fill), "entry_min": now_min,
            "wing": chosen["wing_width"], "call": "open", "put": "open",
            "retry": {"call": 0, "put": 0}}


def _place_close(client, sid, ic, side, cost, log, now_et, retry=False):
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
    log(f"{now_et} {'STOP-retry' if retry else 'STOP'} {side} cost {cost:.2f} bid {price} "
        f"http {st} fill {fp}")


# ---------------------------------------------------------------------------
# One-day driver
# ---------------------------------------------------------------------------

def run_day(date, profile_name="large-spx", cadence=120, dry=False,
            iv_rank=_IV_RANK_PLACEHOLDER, client=None, log=print):
    client = client or Client()
    base = paper.load_base_config()
    profiles = paper.load_profiles()
    if profile_name not in profiles:
        raise ValueError(f"unknown profile {profile_name!r}")
    params = paper._merged_params(base, profiles[profile_name])
    if "SPX" not in [s.upper() for s in params.get("symbols", ["SPX"])]:
        raise ValueError(f"{profile_name} does not trade SPX (this backtester is SPX-only)")
    widths = params.get("wing_widths_by_symbol", {}).get("SPX") or [5, 10]
    stop_trig = params["stop_trigger_ratio"]
    max_adj = params.get("max_stop_adjustments_per_ic", 3)
    yymmdd = yymmdd_of(date)

    used = client.usage_percent()
    if used is not None:
        log(f"# rate-limit bucket usage: {used}%")

    sid = client.open_practice(date)
    if not sid:
        raise RuntimeError("could not open practice session (auth/token? run `login`)")
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
    open_ics, todays, last_min, snaps = [], 0, None, 0

    while cur <= end:
        utc = cur.astimezone(timezone.utc)
        client.set_clock(sid, utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
        now_et = cur.strftime("%H:%M")
        now_min = cur.hour * 60 + cur.minute
        spot, vix = lookup(int(utc.timestamp()))

        # 1. stop management off FREE position marks (reconcile first = fill confirmation)
        if not dry and open_ics:
            marks = mark_map(client.positions(sid))
            for ic in open_ics:
                reconcile(ic, marks)
                for side in ("call", "put"):
                    if ic[side] == "open":
                        cost = side_cost(ic, side, marks)
                        if cost is not None and cost >= stop_trig * ic["net_credit"]:
                            _place_close(client, sid, ic, side, cost, log, now_et)
                    elif ic[side] == "pending_close":
                        cost = side_cost(ic, side, marks)  # still marked => not yet filled
                        if cost is not None:
                            ic["retry"][side] += 1
                            if ic["retry"][side] <= max_adj:
                                _place_close(client, sid, ic, side, cost, log, now_et, retry=True)
                            else:
                                log(f"{now_et} WARN {side} close unconfirmed after {max_adj} retries")

        # 2. entry — one metered snapshot, this profile only (Phase 2 shares it across profiles)
        account_open = sum(1 for ic in open_ics if ic["call"] == "open" or ic["put"] == "open")
        eligible = (600 <= now_min < 870
                    and account_open < params["max_concurrent_ics"]
                    and todays < params.get("daily_ic_trade_target", 10 ** 9)
                    and (last_min is None or now_min - last_min >= params.get("min_minutes_between_entries", 0)))
        if eligible:
            snap = client.snapshot(utc.strftime("%Y-%m-%dT%H:%M:%S"))
            snaps += 1
            cands = build_candidates(snap, spot, widths, _TARGET_DELTA, yymmdd)
            view = {"symbol": "SPX", "date": date, "now_et": now_et, "dte": 0,
                    "underlying_price": spot, "iv_rank": iv_rank, "vix": vix,
                    "vix1d_ratio": None, "atr_5day": None,
                    "session_quality": session_quality(now_min), "gex": {"ok": False},
                    "candidates": cands, "leg_quotes": {}}
            overlap = [{"put_strike": ic["legs"]["sp_k"], "call_strike": ic["legs"]["sc_k"]}
                       for ic in open_ics if not (ic["call"] == "closed" and ic["put"] == "closed")]
            entered, reason, chosen = paper.evaluate_entry(
                view, params, overlap, account_open_count=account_open,
                todays_entry_count=todays, last_entry_min=last_min)
            if entered and dry:
                log(f"{now_et} DRY would ENTER {chosen['wing_width']}w credit~{chosen['ic_natural_bid']}")
                todays += 1
                last_min = now_min
            elif entered:
                ic = _place_ic(client, sid, chosen, yymmdd, now_min, log)
                if ic:
                    open_ics.append(ic)
                    todays += 1
                    last_min = now_min
                    log(f"{now_et} ENTER {ic['wing']}w sp{ic['legs']['sp_k']:.0f}/sc{ic['legs']['sc_k']:.0f} "
                        f"credit {ic['net_credit']}")
                else:
                    log(f"{now_et} ENTER failed (no fill confirmed)")
        cur += timedelta(seconds=cadence)

    # 3. settlement — advance to the close; SPX cash-settles server-side
    client.set_clock(sid, end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    sess = client.session(sid)
    txns = client.transactions(sid)
    summary = {
        "date": date, "profile": profile_name, "dry": dry,
        "entries": todays, "metered_snapshots": snaps + 1,  # +1 historical series
        "realized_pnl": sess.get("equity_options_realized_profit_loss") if isinstance(sess, dict) else None,
        "fees": sess.get("equity_options_fees") if isinstance(sess, dict) else None,
        "day_pnl": sess.get("profit_loss") if isinstance(sess, dict) else None,
        "nlv": sess.get("net_liquidation_value") if isinstance(sess, dict) else None,
        "transactions": len(txns) if isinstance(txns, list) else None,
        "cost_basis": "0dtespx", "iv_rank_source": "placeholder", "sid": sid,
    }
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="0DTESPX practice-session MEIC backtester (Phase 1)")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Backtest one SPX day through a practice session")
    p_run.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_run.add_argument("--profile", default="large-spx", help="SPX-trading profile (default large-spx)")
    p_run.add_argument("--cadence", type=int, default=120, help="clock step seconds (live loop uses 120)")
    p_run.add_argument("--dry", action="store_true", help="log intended entries without placing orders")

    sub.add_parser("status", help="Print the rate-limit bucket usage_percent")

    p_login = sub.add_parser("login", help="Store a 0DTESPX token (delegates to paper_replay)")
    p_login.add_argument("--email", required=True)
    p_login.add_argument("--code", default=None)

    args = parser.parse_args()

    if args.command == "run":
        summary = run_day(args.date, args.profile, cadence=args.cadence, dry=args.dry)
        print(json.dumps({"ok": True, "summary": summary}, default=str))
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
