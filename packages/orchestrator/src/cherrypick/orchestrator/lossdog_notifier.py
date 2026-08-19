"""Notify Lossdog VIP trade-feed trades (to the follow Discord channel) as they sync.

Polls the Lossdog VIP portfolio feed and pushes one card per newly seen trade: a plain text block
for the log floor and a colored Discord embed for the channel this is actually read in
(`format_trade`/`build_embed` below). Same event contract as follow_notifier: a per-id watermark
(one-shot, never re-notified), atomic state, a single-writer lock, and seed-don't-backfill on first
activation so switching it on doesn't blast a hundred historical trades in one burst.

**The card is laid out like the follow-feed card** (`follow_notifier.build_embed`) — same lifecycle
word in the title, same three inline fields (Trade / Context / Stats), same compact signed-leg
structure and db/cr price — because both feeds land in the same Discord channel and a reader should
not have to switch formats mid-scroll. The code is deliberately NOT shared: follow_notifier's
formatting half is kept byte-identical to a sibling repo, so importing it here would make every
change to this card a two-repo change. Where the feeds differ, so do the cards, and only where the
data does: this feed publishes no underlying price, IV rank or POP (Context is expiry alone, Stats
says what kind of trade it is) and no trader comment (the body slot holds the per-leg detail
instead), and its expiries keep their year because it runs far-dated.

**The API is private and undocumented** (reverse-engineered from the logged-in web app at
app.lossdog.com); scripted polling may not be permitted under Lossdog's terms of service. This is a
personal convenience, used at the operator's own risk, and every request is wrapped so a shape
change or an outage degrades to "no notifications", never to a failed job.

Endpoints and their verified quirks (do not re-derive — each of these cost a live experiment):
  GET https://api.app.lossdog.com/portfolio-vip/trades?page=<n>&limit=<1-100>
    Authorization: Bearer <Clerk JWT>   (cookie auth does NOT work; limit=101 is an HTTP 400)
  - Sorted ASCENDING by executionTime with no server-side sort or filter params (all silently
    ignored) — page 1 is the OLDEST trades, the newest live on the LAST page.
  - `syncedAt` lags `executionTime` by up to days and trades land in batches, so timestamps are
    never a watermark here; dedupe is by the stable `id` alone.

**Auth is a ~24h Clerk session JWT, minted per run** from the long-lived `__client` cookie stored
in the OS keyring (`cherrypick secrets-set --channel lossdog`; capture steps in
docs/configuration-and-storage.md). The minted JWT lives only in process memory for the cycle —
never in state, config, or logs. Fallback when the cookie is missing or minting fails: the
LOSSDOG_TOKEN env var, read from the process env with a live HKCU\\Environment (registry) read each
run so a `setx LOSSDOG_TOKEN ...` rotation reaches the supervisor's child jobs without a daemon
restart — `setx` writes the registry, and a long-running daemon's own env block never updates.

**This notifier makes network calls, so it never rides the watchdog tick** — it is its own
supervisor job (`lossdog-notify`), the same treatment as follow_notifier and for the same reason:
the reliability path stays network-free. A dead feed, a dead token, a Clerk API drift, or an
OS keyring that will not answer degrades to "no notifications". A rejected credential warns ONCE
(keyed on the credential itself) and then stays silent until the credential changes, so a 10-minute
cadence cannot become an alert storm; an unreadable keyring says nothing at all until it has stayed
unreadable for _KEYRING_OUTAGE_RUNS runs, because "I could not ask" is not "you never stored it".
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from cherrypick.notify import Notifier
from cherrypick.notify import secrets as notify_secrets

from . import config as cfgmod
from . import util

BASE_URL = "https://api.app.lossdog.com"
_TRADES_PATH = "/portfolio-vip/trades"
_FEED_URL = "https://app.lossdog.com/?p=/trade-feed/"

CLERK_BASE_URL = "https://clerk.lossdog.com"
_CLERK_TEMPLATE = "canis-amnis"  # the JWT template the web app itself mints with
# Clerk's frontend API requires a _clerk_js_version query param; this pin only needs to look like a
# plausible clerk-js release. If Clerk starts rejecting it, bump it to whatever the web app sends.
_CLERK_JS_VERSION = "5.88.0"

_STATE = cfgmod.STATE_DIR / "lossdog_notify.json"
_LOCK = cfgmod.STATE_DIR / "lossdog_notify.lock"
_LOCK_STALE_SECONDS = 600  # a crashed holder must not wedge lossdog notification forever
_ID_CAP = 4000  # bound the remembered-id list
_HTTP_TIMEOUT = 8
_PAGE_LIMIT = 100  # the server's max; limit=101 is a validation error
_DEFAULT_MAX_PER_RUN = 8
_EXPIRY_WARN_HOURS = 2.0
# Consecutive runs the OS keyring may refuse before it stops being a hiccup and becomes an
# outage worth waking someone for. At the 10-minute job cadence that is an hour of silence.
_KEYRING_OUTAGE_RUNS = 6
_LATE_SYNC_HOURS = 24.0

# Sent explicitly: the default "Python-urllib" User-Agent is the sort of thing a CDN blocks, and an
# unattributed poller against someone else's service is bad manners.
_USER_AGENT = "cherrypick-lossdog-notifier/1.0"

# Distinct from the outage None: a 401 means the credential is dead and retrying is pointless,
# where an outage means "ask again next tick". Conflating them is how a dead token spins silently.
AUTH_FAILED = object()

# Stripe colors for the Discord embed, keyed on the trade's own debit/credit label. Tailwind-500s,
# like the suite's existing stripes (0x3B82F6 / 0xF59E0B / 0x8B5CF6). Debit is red-500 rather than
# the orange the source UI leans toward: this feed shares a channel with the follow feed, whose
# CLOSE stripe is amber, and orange next to amber blurs into one color at Discord's stripe width.
COLOR_CREDIT = 0x22C55E  # green — money came in
COLOR_DEBIT = 0xEF4444  # red — money went out

_PRICE_SUFFIX = {"credit": "cr", "debit": "db"}  # the follow card's abbreviations

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_ACTIONS = {
    "BUY_TO_OPEN": "BTO",
    "SELL_TO_OPEN": "STO",
    "BUY_TO_CLOSE": "BTC",
    "SELL_TO_CLOSE": "STC",
}


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
    # Atomic replace: the job could overlap a manual `cherrypick notify-lossdog`, and a plain
    # truncate-then-write could leave a half-written state file.
    cfgmod.ensure_dirs()
    tmp = _STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _STATE)


# --------------------------------------------------------------------------- token
def _token_claims(token: str) -> dict:
    """The JWT payload, decoded locally without signature verification — this is telemetry (exp,
    jti), not authentication; the API is the verifier. {} on anything that isn't a readable JWT."""
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)  # JWT segments drop base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return claims if isinstance(claims, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}


def _token_fingerprint(token: str | None) -> str:
    """A stable, loggable identity for a credential — what the warn-once state is keyed on. Never
    the credential itself: `jti` when the JWT has one, a hash prefix otherwise, 'missing' for none."""
    if not token:
        return "missing"
    jti = _token_claims(token).get("jti")
    if jti:
        return str(jti)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _hours_to_expiry(token: str) -> float | None:
    exp = _token_claims(token).get("exp")
    try:
        return (float(exp) - time.time()) / 3600.0
    except (TypeError, ValueError):
        return None


def _registry_token() -> str | None:
    """LOSSDOG_TOKEN from HKCU\\Environment, read live. `setx` writes here; a running daemon's env
    block never updates, so this read — repeated every run, never cached — is what lets a token
    rotation reach the supervisor's children without a restart."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value = winreg.QueryValueEx(key, "LOSSDOG_TOKEN")[0]
        return str(value).strip() or None
    except OSError:
        return None


def _env_token() -> str | None:
    """The manual-fallback token: process env first (a foreground shell export wins), registry
    second (the setx rotation path)."""
    token = (os.environ.get("LOSSDOG_TOKEN") or "").strip()
    return token or _registry_token()


def _clerk_json(cookie: str, path: str, *, method: str = "GET") -> Any:
    """One Clerk frontend-API call. dict on success, AUTH_FAILED on 401 (the cookie is dead —
    re-login and re-capture), None on anything else — Clerk drifting its API shape must degrade to
    the manual token path, never crash the cycle."""
    url = f"{CLERK_BASE_URL}{path}?_clerk_js_version={_CLERK_JS_VERSION}"
    req = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        method=method,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            # Clerk's FAPI accepts the client JWT as either the cookie or an Authorization header;
            # sending both sidesteps guessing which this deployment honors.
            "Cookie": f"__client={cookie}",
            "Authorization": cookie,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if not 200 <= resp.status < 300:
                return None
            body = json.loads(resp.read().decode("utf-8"))
            return body if isinstance(body, dict) else None
    except urllib.error.HTTPError as exc:  # before URLError: HTTPError subclasses it
        return AUTH_FAILED if exc.code == 401 else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _mint_token(cookie: str) -> Any:
    """A fresh ~24h feed JWT from the keyring __client cookie: find the live Clerk session, then
    mint against the web app's own template. str on success, AUTH_FAILED when the cookie is dead,
    None when Clerk is unreachable or the response shape moved."""
    client = _clerk_json(cookie, "/v1/client")
    if client is AUTH_FAILED:
        return AUTH_FAILED
    if not isinstance(client, dict):
        return None
    body = client.get("response") if isinstance(client.get("response"), dict) else client
    session_id = body.get("last_active_session_id")
    if not session_id:
        sessions = body.get("sessions") or []
        session_id = sessions[0].get("id") if sessions and isinstance(sessions[0], dict) else None
    if not session_id:
        return None
    minted = _clerk_json(cookie, f"/v1/client/sessions/{session_id}/tokens/{_CLERK_TEMPLATE}", method="POST")
    if minted is AUTH_FAILED:
        return AUTH_FAILED
    if not isinstance(minted, dict):
        return None
    jwt = minted.get("jwt")
    if not jwt and isinstance(minted.get("response"), dict):
        jwt = minted["response"].get("jwt")
    return str(jwt) if jwt else None


# --------------------------------------------------------------------------- feed access
def _get_page(token: str, page: int, limit: int) -> Any:
    """GET one trades page. dict on success, AUTH_FAILED on 401, None on any other failure. 403
    deliberately maps to outage, not auth: Cloudflare bot-blocks are 403s, and a false "token dead"
    alarm is worse than a silent retry next tick."""
    url = f"{BASE_URL}{_TRADES_PATH}?page={int(page)}&limit={int(limit)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            if not 200 <= resp.status < 300:
                return None
            body = json.loads(resp.read().decode("utf-8"))
            return body if isinstance(body, dict) else None
    except urllib.error.HTTPError as exc:  # before URLError: HTTPError subclasses it
        return AUTH_FAILED if exc.code == 401 else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _page_items(body: dict) -> list[dict]:
    return [t for t in body.get("items") or [] if isinstance(t, dict) and t.get("id")]


def fetch_new_trades(token: str, seen: set[str], last_total: int | None) -> Any:
    """Everything past the watermark, walking BACKWARDS from the last page (the feed sorts
    ascending, so that's where the new trades are). (trades, totalItems) on success, None on outage,
    AUTH_FAILED on 401.

    The cheap probe first: page=1&limit=1 carries totalItems, and an unchanged total means no walk
    at all — the common case at a 10-minute cadence. Otherwise pages are collected newest-first
    until one contains nothing unseen; the id-keyed dict absorbs the page-boundary shifts a
    mid-list insertion causes. A mid-walk outage aborts the WHOLE cycle with state untouched —
    a partial window must not watermark trades it never rendered."""
    head = _get_page(token, 1, 1)
    if head is AUTH_FAILED:
        return AUTH_FAILED
    if not isinstance(head, dict):
        return None
    try:
        total = int(head.get("totalItems") or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return [], total
    if last_total is not None and total == int(last_total):
        return [], total

    collected: dict[str, dict] = {}
    for page in range(max(1, math.ceil(total / _PAGE_LIMIT)), 0, -1):
        body = _get_page(token, page, _PAGE_LIMIT)
        if body is AUTH_FAILED:
            return AUTH_FAILED
        if not isinstance(body, dict):
            return None
        items = _page_items(body)
        fresh = [t for t in items if str(t["id"]) not in seen and str(t["id"]) not in collected]
        collected.update({str(t["id"]): t for t in fresh})
        if items and not fresh:
            break  # a fully-seen page: everything older is watermarked too
    return list(collected.values()), total


def fetch_last_trades(token: str, count: int) -> Any:
    """The `count` most recent trades regardless of any watermark — the --replay-last affordance.
    Same return contract as fetch_new_trades, without the unchanged-total short-circuit."""
    head = _get_page(token, 1, 1)
    if head is AUTH_FAILED:
        return AUTH_FAILED
    if not isinstance(head, dict):
        return None
    try:
        total = int(head.get("totalItems") or 0)
    except (TypeError, ValueError):
        return None
    collected: dict[str, dict] = {}
    for page in range(max(1, math.ceil(total / _PAGE_LIMIT)), 0, -1):
        if len(collected) >= count or total <= 0:
            break
        body = _get_page(token, page, _PAGE_LIMIT)
        if body is AUTH_FAILED:
            return AUTH_FAILED
        if not isinstance(body, dict):
            return None
        collected.update({str(t["id"]): t for t in _page_items(body)})
    newest = sorted(collected.values(), key=_sort_key)[-max(0, count) :]
    return newest, total


# --------------------------------------------------------------------------- filters
def _matches_filters(trade: dict, filters: dict) -> bool:
    """Local narrowing — the API silently ignores every filter param, so unlike the follow feed the
    narrowing cannot happen server-side. A filtered-out trade is watermarked silently, which nets
    out to the same behavior the follow feed gets from its query params."""
    traders = [str(t).strip().lower() for t in filters.get("traders") or [] if str(t).strip()]
    if traders:
        name = str((trade.get("trader") or {}).get("name") or "").strip().lower()
        if name not in traders:
            return False
    symbols = [str(s).strip().upper() for s in filters.get("underlying_symbols") or [] if str(s).strip()]
    if symbols and str(trade.get("underlyingSymbol") or "").upper() not in symbols:
        return False
    strategy = str(filters.get("strategy") or "").strip().lower()
    if strategy:
        slug = str(trade.get("strategySlug") or "").lower()
        name = str(trade.get("strategyName") or "").lower()
        if strategy not in (slug, name):
            return False
    open_close = filters.get("open_close")
    if open_close in ("O", "C") and _open_close(trade) != open_close:
        return False
    return True


# --------------------------------------------------------------------------- formatting
def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trim(value) -> str:
    """A number without trailing zeros: 360.0 -> '360', 457.5 -> '457.5'."""
    n = _num(value)
    return f"{n:g}" if n is not None else str(value)


def _money(value) -> str:
    """Cash to the cent: 1.5 -> '1.50'. `_trim` is right for a strike ($61, not $61.00) and wrong
    for money, where a stripped zero reads as a typo. A sub-cent value keeps its own precision
    rather than being rounded away — an average fill across partials can land at 0.055."""
    n = _num(value)
    if n is None:
        return str(value)
    cents = f"{n:.2f}"
    return cents if float(cents) == n else f"{n:g}"


def _action_abbrev(action) -> str:
    """The trader shorthand everyone reads: BUY_TO_OPEN -> BTO. Unknown actions render as words
    rather than vanishing — the strategy-slug list is open-ended and the action list probably is too."""
    raw = str(action or "").strip().upper()
    if not raw:
        return ""
    return _ACTIONS.get(raw, raw.replace("_", " ").title())


def _open_close(trade: dict) -> str:
    """'O' / 'C' / '' from the legs' actions. A roll touches both sides, so anything mixed gets no
    tag rather than a misleading one."""
    marks = set()
    for leg in trade.get("legs") or []:
        action = str(leg.get("action") or "").upper()
        if action.endswith("_TO_OPEN"):
            marks.add("O")
        elif action.endswith("_TO_CLOSE"):
            marks.add("C")
        elif action:
            marks.add("?")
    if marks == {"O"}:
        return "O"
    if marks == {"C"}:
        return "C"
    return ""


def _lifecycle(trade: dict) -> tuple[str, str]:
    """(marker, headline word) — the same OPEN/CLOSE/ROLL vocabulary the follow-feed card uses, so
    the two feeds read as one stream in the channel. The color stays on the cash direction
    (credit/debit), which is an independent fact from the lifecycle."""
    match _open_close(trade):
        case "O":
            return "➕", "OPEN"
        case "C":
            return "➖", "CLOSE"
        case _:
            legs = trade.get("legs") or []
            actions = {str(leg.get("action") or "").upper() for leg in legs}
            mixed = any(a.endswith("_TO_OPEN") for a in actions) and any(
                a.endswith("_TO_CLOSE") for a in actions
            )
            return "🔁", "ROLL" if mixed else ""


def _leg_sign(leg: dict) -> str:
    """'+' long, '-' short, '' when the feed didn't say — the most load-bearing character on the
    compact line. '360C/350C' is a debit spread long the 360 and a credit spread short it, and the
    net db/cr can't settle it (buying back a credit spread is a debit)."""
    action = str(leg.get("action") or "").upper()
    if action.startswith("SELL"):
        return "-"
    if action.startswith("BUY"):
        return "+"
    return ""


def _leg_body(leg: dict) -> str:
    """One leg as strike+right, e.g. '360C'. A strike-less leg (stock) falls back to its symbol —
    `_legs_summary` then drops it, because the title already carries the underlying."""
    strike = leg.get("strike")
    right = str(leg.get("callOrPut") or leg.get("optionType") or "").upper()
    if strike is None or not right:
        return str(leg.get("symbol") or leg.get("underlyingSymbol") or "?")
    return f"{_trim(strike)}{right[0]}"


def _is_stock_leg(leg: dict) -> bool:
    return leg.get("strike") is None or not (leg.get("callOrPut") or leg.get("optionType"))


def _quantity(trade: dict) -> str:
    """Contract count as '2x'. Three-plus legs use the legs' GCD, which recovers how many times the
    base structure was traded: a 1-lot butterfly's body leg is 2 against two 1-lot wings, twice the
    wing size by construction, not because two butterflies were bought. A two-leg combo keeps its
    largest leg — a genuine 1x3 ratio has no base structure for a GCD to recover cleanly. An
    all-stock trade counts in shares: '100x' would read as a 100-lot, a 100x overstatement."""
    legs = trade.get("legs") or []
    sizes = [q for q in (_num(leg.get("unitQuantity")) for leg in legs) if q]
    if not sizes:
        return ""
    if all(_is_stock_leg(leg) for leg in legs):
        return f"{max(sizes):g} sh"
    if len(sizes) >= 3 and all(s.is_integer() for s in sizes):
        return f"{math.gcd(*(int(s) for s in sizes)):g}×"
    return f"{max(sizes):g}×"


def _common_sign(trade: dict) -> str:
    """The one sign shared by every leg, or '' if they disagree or the feed didn't say."""
    signs = {_leg_sign(leg) for leg in trade.get("legs") or []}
    return signs.pop() if len(signs) == 1 else ""


def _legs_summary(trade: dict, max_legs: int = 6) -> str:
    """'-360C/+370C'. Stock legs contribute nothing here — their body IS the underlying the title
    already carries, so rendering both gave 'TSLA TSLA'."""
    legs = trade.get("legs") or []
    tokens = [f"{_leg_sign(leg)}{_leg_body(leg)}" for leg in legs[:max_legs] if not _is_stock_leg(leg)]
    if not tokens:
        return ""
    shown = "/".join(tokens)
    if len(legs) > max_legs:
        shown += f"/+{len(legs) - max_legs}"
    return shown


def _structure(trade: dict) -> str:
    """Size and legs together: '1× -360C/+370C'. Either half alone is fine. When no leg token
    survives (stock), the size takes the sign instead, so '-100 sh' still says which way they went.
    The per-leg detail is not lost — it stays in the card body, one leg per line."""
    qty, legs = _quantity(trade), _legs_summary(trade)
    if qty and not legs:
        qty = _common_sign(trade) + qty
    return " ".join(p for p in (qty, legs) if p)


def _stats_field(trade: dict) -> str:
    """The card's third column: what kind of instrument, and how many legs. The follow feed spends
    this slot on IV rank and POP; this feed publishes neither, and inventing them would be worse
    than saying what it does know."""
    legs = trade.get("legs") or []
    parts = [str(trade.get("assetType") or "").strip()]
    if legs:
        parts.append(f"{len(legs)} leg" + ("s" if len(legs) != 1 else ""))
    return " · ".join(p for p in parts if p)


def _date_label(iso) -> str:
    """'2026-08-21' -> '21 Aug 26'. Falls back to the raw string on anything unparseable — this
    feed carries far-dated expiries, so unlike the follow feed the year stays."""
    try:
        y, m, d = (int(part) for part in str(iso).split("-")[:3])
        return f"{d} {_MONTHS[m - 1]} {y % 100:02d}"
    except (ValueError, IndexError):
        return str(iso)


def _expiries(trade: dict) -> list[str]:
    legs = trade.get("legs") or []
    return sorted({str(leg["expirationDate"]) for leg in legs if leg.get("expirationDate")})


def _dte(trade: dict) -> int | None:
    values = [leg.get("dte") for leg in trade.get("legs") or [] if leg.get("dte") is not None]
    try:
        return min(int(v) for v in values) if values else None
    except (TypeError, ValueError):
        return None


def _expiry_field(trade: dict) -> str:
    """'21 Aug 26 · 18 DTE', or '19 Sep 26 – 17 Oct 26' for a calendar; '' for stock. The DTE only
    accompanies a single expiry — on a calendar it would be ambiguous about which leg it describes."""
    exps = _expiries(trade)
    if not exps:
        return ""
    if len(exps) > 1:
        return f"{_date_label(exps[0])} – {_date_label(exps[-1])}"
    label = _date_label(exps[0])
    dte = _dte(trade)
    return f"{label} · {dte} DTE" if dte is not None else label


def _leg_line(leg: dict) -> str:
    """One leg the way a trader would say it aloud: 'BTC 1× 21 Aug 26 $360 CALL @ $3.26 · 18 DTE'.
    A stock leg has no strike or expiry, so it collapses to 'BTO 100 sh @ $174.23' — '100×' on
    shares would read as a 100-lot of contracts, a 100x overstatement."""
    abbrev = _action_abbrev(leg.get("action"))
    qty = _num(leg.get("unitQuantity"))
    fill = leg.get("averageFillPrice")
    right = str(leg.get("callOrPut") or leg.get("optionType") or "").upper()
    strike = leg.get("strike")
    if strike is None or not right:  # stock (or anything strike-less the feed invents later)
        parts = [abbrev, f"{qty:g} sh" if qty is not None else ""]
        if fill is not None:
            parts.append(f"@ ${_money(fill)}")
        return " ".join(p for p in parts if p)
    parts = [abbrev, f"{qty:g}×" if qty is not None else ""]
    exp = leg.get("expirationDate")
    if exp:
        parts.append(_date_label(exp))
    parts.append(f"${_trim(strike)} {right}")
    if fill is not None:
        parts.append(f"@ ${_money(fill)}")
    line = " ".join(p for p in parts if p)
    dte = leg.get("dte")
    if dte is not None:
        line += f" · {dte} DTE"
    return line


def _price_line(trade: dict) -> str:
    """'$3.26 db' — the follow card's abbreviation, so the two feeds' price columns line up."""
    price = trade.get("price")
    if price in (None, ""):
        return ""
    label = str(trade.get("priceLabel") or "").strip().lower()
    return f"${_money(price)} {_PRICE_SUFFIX.get(label, label)}".strip()


def _strategy(trade: dict) -> str:
    """strategyName leads — the slug list is open-ended, and the name is the human string the feed
    already renders. The slug is only a fallback, un-snaked."""
    name = str(trade.get("strategyName") or "").strip()
    if name:
        return name
    slug = str(trade.get("strategySlug") or "").strip()
    return slug.replace("_", " ").title() if slug else "Trade"


def _parse_ts(raw) -> datetime | None:
    try:  # fromisoformat only learned to parse a trailing 'Z' in 3.11
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _execution_ts(trade: dict) -> datetime | None:
    return _parse_ts(trade.get("executionTime"))


def _body_is_redundant(trade: dict) -> bool:
    """True when the leg lines would repeat the fields and add nothing.

    Only ever on a single-leg trade, and only when the compressed structure really does carry the
    whole leg: '1× -61C · $1.50 cr' against 'STO 1× 18 Sep 26 $61 CALL @ $1.50 · 31 DTE' is the same
    sentence twice, since the date and DTE are already the Context column and one leg's fill IS the
    net price. Two legs is never redundant — the per-leg fills and the pairing of each strike to its
    own expiry exist nowhere else on the card.

    The two guards are what keep this from silently eating information. A leg whose `action` the feed
    doesn't tag as a buy or a sell (an exercise, an assignment) has no sign in the structure, so the
    body is the only place its verb appears. And a fill that disagrees with the trade's net price is
    telling us something we can't reconstruct, whatever the reason — so it stays on the card."""
    legs = trade.get("legs") or []
    if len(legs) != 1:
        return False
    leg = legs[0]
    if not _leg_sign(leg):
        return False
    fill, price = leg.get("averageFillPrice"), trade.get("price")
    return fill is None or price in (None, "") or _money(fill) == _money(price)


def _late_sync_note(trade: dict) -> str:
    """'synced 4d after execution' when syncedAt lags executionTime by more than a day — the feed
    backfills in batches, and a card arriving today for last week's fill should say so."""
    executed, synced = _execution_ts(trade), _parse_ts(trade.get("syncedAt"))
    if executed is None or synced is None:
        return ""
    hours = (synced - executed).total_seconds() / 3600.0
    if hours <= _LATE_SYNC_HOURS:
        return ""
    return f"synced {max(1, round(hours / 24.0))}d after execution"


def _trader_name(trade: dict) -> str:
    return str((trade.get("trader") or {}).get("name") or "Lossdog trader")


def _sort_key(trade: dict) -> tuple:
    """Chronological, so a multi-trade run reads in the order the trades happened. `executionTime`
    is the platform's own timestamp; `id` breaks ties and covers a null."""
    return (str(trade.get("executionTime") or ""), str(trade.get("id") or ""))


def _valid_trade(trade) -> bool:
    return isinstance(trade, dict) and bool(trade.get("id"))


def format_trade(trade: dict) -> str:
    """One trade as plain text — the log floor and any non-Discord channel read this. Head line,
    numbers line, then the legs quoted one per line. Kept UTC (plain text has no way to render in
    the reader's timezone; the Discord embed does that properly)."""
    marker, word = _lifecycle(trade)
    symbol = str(trade.get("underlyingSymbol") or "?")
    head = f"{marker} {_trader_name(trade)} · {word} {symbol} {_strategy(trade)}".replace("  ", " ")

    when = _execution_ts(trade)
    detail = [
        _structure(trade),
        f"exp {_expiry_field(trade)}" if _expiry_field(trade) else "",
        _price_line(trade),
        _stats_field(trade),
        when.astimezone(timezone.utc).strftime("%H:%M UTC") if when else "",
    ]
    lines = [head, " · ".join(p for p in detail if p)]
    legs = trade.get("legs") or []
    lines.extend(f"> {_leg_line(leg)}" for leg in legs[:6] if _leg_line(leg))
    if len(legs) > 6:
        lines.append(f"> +{len(legs) - 6} more legs")
    note = _late_sync_note(trade)
    if note:
        lines.append(note)
    return "\n".join(ln for ln in lines if ln)


def _embed_field(name: str, value: str) -> dict | None:
    return {"name": name, "value": value, "inline": True} if value else None


def build_embed(trade: dict) -> dict:
    """One trade as a Discord embed, in the follow-feed card's shape: lifecycle word and underlying
    in the title, the numbers grouped into three inline fields — Trade, Context, Stats — so the card
    is one horizontal strip rather than two wrapped rows, and the legs in the body.

    The two feeds are read side by side in the same channel, so they are laid out the same way. What
    differs is only what each feed actually publishes: this one carries no underlying price, IV rank
    or POP, so Context is expiry alone and Stats says what kind of trade it is; and it carries no
    trader comment, so the body slot the follow card gives the rationale holds the per-leg detail
    instead — the one thing this feed has that the other doesn't, dropped on the single-leg trades
    where it only repeats the fields (see `_body_is_redundant`). Expiries keep their year: this
    feed runs far-dated, where 'Oct 16' would be ambiguous.

    The renderers are deliberately NOT shared with follow_notifier: that module's formatting half is
    kept byte-identical to a sibling repo (see its docstring), and reaching into it from here would
    make every lossdog change a two-repo change. Same silhouette, separate code.

    `timestamp` is the execution time — Discord renders it in each reader's own timezone, which is
    the honest way to show a time on a message that may arrive days after the fact (`syncedAt`
    batches); the footer says so explicitly when the lag exceeds a day."""
    trader = trade.get("trader") or {}
    author_bits = [str(trader.get("name") or "").strip(), str(trader.get("jobPosition") or "").strip()]
    author: dict = {"name": " · ".join(b for b in author_bits if b) or "Lossdog trader"}
    if trader.get("profilePictureUrl"):
        author["icon_url"] = str(trader["profilePictureUrl"])

    _, word = _lifecycle(trade)
    symbol = str(trade.get("underlyingSymbol") or "?")
    title = " · ".join(p for p in (word, f"{symbol} {_strategy(trade)}".strip()) if p)

    trade_field = " · ".join(p for p in (_structure(trade), _price_line(trade)) if p)
    fields = [
        f
        for f in (
            _embed_field("Trade", trade_field),
            _embed_field("Context", _expiry_field(trade)),
            _embed_field("Stats", _stats_field(trade)),
        )
        if f
    ]

    embed: dict = {
        "author": author,
        "title": title[:256],
        "url": _FEED_URL,
        "color": COLOR_CREDIT if str(trade.get("priceLabel") or "") == "credit" else COLOR_DEBIT,
        "fields": fields,
    }
    legs = [] if _body_is_redundant(trade) else (trade.get("legs") or [])
    leg_lines = [_leg_line(leg) for leg in legs[:6] if _leg_line(leg)]
    if len(legs) > 6:
        leg_lines.append(f"+{len(legs) - 6} more legs")
    if leg_lines:
        embed["description"] = "\n".join(leg_lines)[:4096]
    footer = "Lossdog VIP Trade Feed"
    note = _late_sync_note(trade)
    if note:
        footer += f" · {note}"
    embed["footer"] = {"text": footer}
    when = _execution_ts(trade)
    if when:
        embed["timestamp"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return embed


# --------------------------------------------------------------------------- entrypoint
def _resolve_token() -> tuple[str | None, str, str, str | None]:
    """(token, source, credential_fingerprint, dead_cookie_fingerprint). Minted-from-keyring first,
    env/registry fallback. The fingerprint identifies the credential the run leans on — the keyring
    cookie when it minted, the manual token otherwise — so warn-once state survives across ticks
    and re-arms on rotation. `dead_cookie_fingerprint` is set when the cookie was REJECTED (not
    merely unreachable): the run must say so once even while the fallback keeps the feed alive,
    because a silently dead cookie resurfaces as a mystery outage the day the fallback expires."""
    cookie = notify_secrets.get_lossdog_client()
    dead_cookie = None
    keyring_down = cookie is notify_secrets.KEYRING_UNAVAILABLE
    if keyring_down:
        cookie = None
    if cookie:
        minted = _mint_token(cookie)
        if isinstance(minted, str):
            return minted, "minted", _token_fingerprint(cookie), None
        if minted is AUTH_FAILED:
            dead_cookie = _token_fingerprint(cookie)
        # else: Clerk unreachable or shape drift — not the cookie's fault, fall back silently.
    token = _env_token()
    if token:
        return token, "env", _token_fingerprint(token), dead_cookie
    if keyring_down:
        # The cookie may be sitting right there — we could not ask. Say "unavailable", not
        # "missing", so the caller does not accuse the operator of never storing it.
        return None, "keyring_unavailable", "keyring_unavailable", dead_cookie
    return None, "missing", dead_cookie or "missing", dead_cookie


def run(cfg: dict | None = None, *, replay_last: int = 0, dry_run: bool = False) -> dict:
    """One poll cycle. `replay_last`/`dry_run` are testing affordances that bypass the enabled gate
    (so the card can be eyeballed before the job is switched on) and never touch state."""
    cfg = cfgmod.load_config() if cfg is None else cfg  # an explicit {} must stay {}, not fall back
    settings = cfgmod.lossdog_settings(cfg)
    testing = bool(replay_last) or dry_run
    if not settings["enabled"] and not testing:
        return {"ok": True, "skipped": "lossdog not enabled"}

    if not _acquire_lock():
        return {"ok": True, "skipped": "another lossdog-notify run holds the lock"}
    try:
        notify_cfg = cfg.get("notify", {})
        notifier = Notifier({**notify_cfg, "channels": settings["channels"]})
        max_per_run = max(1, int(settings["max_per_run"] or _DEFAULT_MAX_PER_RUN))
        state = util.read_json(_STATE)

        token, source, fingerprint, dead_cookie = _resolve_token()
        summary: dict = {"ok": True, "token_source": source}
        if dead_cookie and token is not None:
            # The keyring cookie was rejected but the manual token is carrying the feed. Say so
            # once per cookie — keyed separately from auth_warned so a later successful fetch
            # (which clears that) cannot re-arm this into a 10-minute nag.
            if not testing and state.get("cookie_warned_fingerprint") != dead_cookie:
                notifier.notify(
                    "WARNING",
                    "lossdog.cookie",
                    "Lossdog __client cookie rejected — running on the manual token",
                    "Clerk returned 401 for the stored cookie. The LOSSDOG_TOKEN fallback is "
                    "carrying the feed for now, but it expires daily: log in at app.lossdog.com "
                    "and re-capture the cookie (cherrypick secrets-set --channel lossdog).",
                )
                state["cookie_warned_fingerprint"] = dead_cookie
                _save_state(state)
        if token is None:
            title = "Lossdog feed has no working credential"
            body = (
                "Store the Clerk __client cookie (cherrypick secrets-set --channel lossdog) or "
                'set a fresh token with `setx LOSSDOG_TOKEN "<jwt>"` — see '
                "docs/configuration-and-storage.md."
            )
            if source == "keyring_unavailable":
                # The keyring refused the read, so the credential is unknown, not absent. Degrade
                # quietly the way a Clerk outage does — but not forever: a keyring that stays down
                # silences the feed, so count the consecutive refusals and speak once they stop
                # being plausibly transient.
                misses = int(state.get("keyring_unavailable_runs") or 0) + 1
                state["keyring_unavailable_runs"] = misses
                _save_state(state)
                summary["keyring_unavailable_runs"] = misses
                if misses < _KEYRING_OUTAGE_RUNS:
                    summary.update({"skipped": "keyring unavailable"})
                    return summary
                title = "Lossdog feed cannot read its credential"
                body = (
                    f"The OS keyring has refused the stored cookie for {misses} runs in a row, so "
                    "the feed is silent. The credential itself may be fine — check that Windows "
                    "Credential Manager is reachable (cherrypick secrets-status)."
                )
            if not testing and state.get("auth_warned_fingerprint") != fingerprint:
                notifier.notify("WARNING", "lossdog.auth", title, body)
                state["auth_warned_fingerprint"] = fingerprint
                _save_state(state)
            summary.update({"skipped": "no credential"})
            return summary
        if state.get("keyring_unavailable_runs"):
            state["keyring_unavailable_runs"] = 0  # the keyring answered — re-arm the outage count
            _save_state(state)

        hours = _hours_to_expiry(token)
        if hours is not None:
            summary["token_hours_left"] = round(hours, 1)
        if (
            not testing
            and source == "env"
            and hours is not None
            and hours < _EXPIRY_WARN_HOURS
            and state.get("expiry_warned_fingerprint") != fingerprint
        ):
            notifier.notify(
                "WARNING",
                "lossdog.token.expiring",
                "Lossdog token expires soon",
                f"The manual LOSSDOG_TOKEN has ~{max(hours, 0):.1f}h left. Mint a fresh one and "
                'rotate it with `setx LOSSDOG_TOKEN "<jwt>"` (no restart needed).',
            )
            state["expiry_warned_fingerprint"] = fingerprint
            _save_state(state)

        # ---- testing affordances: render without watermarking
        if replay_last:
            result = fetch_last_trades(token, int(replay_last))
            if result is AUTH_FAILED or result is None:
                error = "auth failed (401)" if result is AUTH_FAILED else "feed unreachable"
                summary.update({"ok": False, "error": error})
                return summary
            trades, _total = result
            for trade in trades:
                if dry_run:
                    print(json.dumps({"message": format_trade(trade), "embed": build_embed(trade)}, indent=2))
                else:
                    notifier.notify(
                        "INFO",
                        f"lossdog.trade.{trade['id']}",
                        "Lossdog VIP",
                        format_trade(trade),
                        embed=build_embed(trade),
                    )
            summary.update({"replayed": len(trades), "dry_run": dry_run, "state_untouched": True})
            return summary

        seen = {str(i) for i in state.get("notified_ids") or []}
        result = fetch_new_trades(token, seen, state.get("last_total_items"))
        if result is AUTH_FAILED and source == "env":
            # The daemon's env can hold yesterday's token while the registry already has today's —
            # the one case the live registry read exists for. Retry once with it before warning.
            registry = _registry_token()
            if registry and registry != token:
                token, fingerprint = registry, _token_fingerprint(registry)
                result = fetch_new_trades(token, seen, state.get("last_total_items"))
        if result is AUTH_FAILED:
            if not testing and state.get("auth_warned_fingerprint") != fingerprint:
                notifier.notify(
                    "WARNING",
                    "lossdog.auth",
                    "Lossdog credential rejected (401)",
                    "Re-capture the __client cookie (log in at app.lossdog.com, then "
                    "cherrypick secrets-set --channel lossdog) or rotate LOSSDOG_TOKEN via setx. "
                    "This warning will not repeat until the credential changes.",
                )
                state["auth_warned_fingerprint"] = fingerprint
                _save_state(state)
            summary.update({"skipped": "auth failed (401)"})
            return summary
        if result is None:
            # An outage is indistinguishable from an empty feed here, and both mean the same thing:
            # nothing to push, state untouched, try again next tick.
            summary.update({"trades_seen": 0, "notified": 0})
            return summary
        if state.get("auth_warned_fingerprint"):
            state["auth_warned_fingerprint"] = None  # the credential proved good — re-arm the warning

        trades, total = result
        valid = [t for t in trades if _valid_trade(t)]
        malformed = len(trades) - len(valid)

        if not seen:  # first activation — seed, don't backfill
            if dry_run:
                summary.update({"dry_run": True, "would_seed": True, "trades_seen": len(valid)})
                return summary
            state["notified_ids"] = [str(t["id"]) for t in sorted(valid, key=_sort_key)]
            state["last_total_items"] = total
            _save_state(state)
            summary.update({"seeded": True, "trades_seen": len(valid)})
            return summary

        fresh = sorted(valid, key=_sort_key)
        matching = [t for t in fresh if _matches_filters(t, settings["filters"])]
        filtered = len(fresh) - len(matching)

        # After downtime the whole gap is "new". Push the newest max_per_run and watermark the
        # older ones silently, so an outage costs the tail rather than a flood.
        suppressed = matching[:-max_per_run] if len(matching) > max_per_run else []
        to_push = matching[-max_per_run:]

        if dry_run:
            for trade in to_push:
                print(json.dumps({"message": format_trade(trade), "embed": build_embed(trade)}, indent=2))
            summary.update(
                {
                    "dry_run": True,
                    "state_untouched": True,
                    "trades_seen": len(trades),
                    "would_notify": len(to_push),
                    "suppressed": len(suppressed),
                    "filtered": filtered,
                    "malformed": malformed,
                }
            )
            return summary

        for trade in to_push:
            notifier.notify(
                "INFO",
                f"lossdog.trade.{trade['id']}",
                "Lossdog VIP",
                format_trade(trade),
                embed=build_embed(trade),
            )
        # Watermark everything valid this cycle — pushed, suppressed, and filtered alike. Malformed
        # trades are deliberately NOT marked, so a transiently mangled trade gets another look.
        ids = [str(i) for i in state.get("notified_ids") or []]
        known = set(ids)
        ids.extend(str(t["id"]) for t in fresh if str(t["id"]) not in known)
        state["notified_ids"] = ids[-_ID_CAP:]  # insertion-ordered cap: these ids don't sort
        state["last_total_items"] = total
        _save_state(state)
        summary.update(
            {
                "trades_seen": len(trades),
                "notified": len(to_push),
                "suppressed": len(suppressed),
                "filtered": filtered,
                "malformed": malformed,
            }
        )
        return summary
    finally:
        _release_lock()
