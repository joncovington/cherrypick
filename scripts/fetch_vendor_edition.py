"""Collect the vendor's daily pre-open report as a scored fixture for the market-report build.

**Why this exists.** `docs/market-report-plan.md` rebuilds a commercial pre-open report from our own
data, and every engine it plans is scored against the vendor's published editions. Five were saved
by hand; each one settled something the others could not. The fixture only grows if collecting it
does not depend on someone remembering to save a page, so this does it every morning (Phase 0b).

A script rather than package code, for the same reason as the narratives and the Dolt refresh: it
reaches the network, and nothing on a decision path may. It writes only its own store,
`~/.cherrypick/data/market-report/`, and a failure leaves every saved edition exactly as it was.

**It is deliberately slow.** One browser session per run, at most one login attempt, a randomized
pause of 20-45 seconds between opening one report and the next, and a backfill capped at a few
editions per run. A throttling or refusal response (HTTP 429 or 403) from the vendor ends the run
at once and starts a 24-hour cooldown that later runs respect: being locked out of a subscription
costs far more than a missed day, which a person can fetch by hand.

**It stops rather than fights.** A captcha, a second-factor prompt or a refused login ends the run
with nothing saved and a notification. It never retries a login and never tries to get past a
challenge; `login` opens a visible browser for a person to sign in, and the session it leaves is
what later runs reuse.

**A file must prove it is an edition before it counts** — dateline matching the header's date, the
"Covers ... closing prices" line, the disclaimer at the end, and leader/laggard totals decoded from
the stage colours equal to the counts the edition states. A file that fails is kept aside as
`YYYY-MM-DD.rejected.html`, never as the day's fixture. An existing edition is never overwritten.

Nothing that names the vendor is in this file. Two generic keys in the suite config's
`market_report` block say where to read and what the report is called, and the script refuses to run
without both (the scheduler does not even create its jobs):

    "vendor_dashboard_url": "https://...",
    "vendor_edition_title": "<the title before the report's date>"

Credentials live only in the OS keyring (service `cherrypick-vendor-report`).

    python scripts/fetch_vendor_edition.py credentials       # store username/password once
    python scripts/fetch_vendor_edition.py login             # sign in by hand, then run missed fetches
    python scripts/fetch_vendor_edition.py session-alert     # hourly expired-session reminder, offline
    python scripts/fetch_vendor_edition.py edition [--backfill N] [--headed]
    python scripts/fetch_vendor_edition.py charts [TICKER...]  # evening: screeners, then chart pages
    python scripts/fetch_vendor_edition.py screeners         # the three income screener lists only
    python scripts/fetch_vendor_edition.py validate FILE...  # check saved files, offline
    python scripts/fetch_vendor_edition.py probe-chart TICKER  # record a chart page for parser design
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import random
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

KEYRING_SERVICE = "cherrypick-vendor-report"
USERNAME, PASSWORD = "username", "password"

# Pacing. Chosen to look like a person reading, not a crawler: the vendor's terms are silent on
# automated access, and silence is not an invitation to hurry.
PAUSE_RANGE_S = (20.0, 45.0)
# A chart page load makes ~50 requests of its own (the app, its settings, the symbol list), so
# chart pages are spaced further apart than report cards, which open inside a loaded page.
CHART_PAUSE_RANGE_S = (30.0, 60.0)
MAX_BACKFILL = 3
COOLDOWN = timedelta(hours=24)
CARD_READY_CHARS = 5000
CARD_READY_TIMEOUT_S = 90

# The report header the browser looks for: '<vendor_edition_title> - September 25, 2026'. Built from the
# config by load_config(), which every browser path calls first; None until then.
HEADER_RE: re.Pattern[str] | None = None


def header_re(title: str) -> re.Pattern[str]:
    return re.compile(re.escape(title.strip()) + r" - ([A-Z][a-z]+ \d{1,2}, \d{4})")


MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

# The stage colours of the leaders/laggards table. The two sets are disjoint, so a ticker's colour
# alone says which column it is in; decoded counts are checked against the stated ones.
LEADER_COLOURS = {"1B5E20", "2E7D32", "43A047"}
LAGGARD_COLOURS = {"7A0030", "C60651", "E5384F"}
DISCLAIMER = "Characteristics and Risks of Standardized Options"


# ------------------------------------------------------------------------------------------------
# Validation: pure functions over saved HTML. No network, no browser, no keyring.
# ------------------------------------------------------------------------------------------------


def parse_long_date(text: str) -> date | None:
    """'September 25, 2026' -> date(2026, 9, 25), or None."""
    m = re.fullmatch(r"\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})\s*", text)
    if not m or m.group(1) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))


def _card_part(page_html: str) -> str:
    """The report itself: everything after the wrapper's <h1>, so the title we write ourselves can
    never satisfy a check meant for the vendor's own dateline."""
    head_end = page_html.find("</h1>")
    return page_html[head_end + 5 :] if head_end >= 0 else page_html


def _text(fragment: str) -> str:
    fragment = re.sub(r"<(style|script)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def breadth_counts(card_html: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """((stated_out, stated_under), (decoded_out, decoded_under)); either side None if absent."""
    stated = None
    so = re.search(r">\s*Outperforming\s*</div>\s*<div[^>]*>\s*(\d+)\s*<", card_html)
    su = re.search(r">\s*Underperforming\s*</div>\s*<div[^>]*>\s*(\d+)\s*<", card_html)
    if so and su:
        stated = (int(so.group(1)), int(su.group(1)))
    start = card_html.find(">Sector</th>")
    end = card_html.find("The three shades", start)
    decoded = None
    if start >= 0 and end > start:
        colours = re.findall(r"color:\s*#([0-9A-Fa-f]{6});[^>]*>\s*[A-Z][A-Z.]*\s*</a>", card_html[start:end])
        up = [c.upper() for c in colours]
        decoded = (sum(c in LEADER_COLOURS for c in up), sum(c in LAGGARD_COLOURS for c in up))
    return stated, decoded


def validate_edition(page_html: str, expected: date) -> list[str]:
    """Every reason this file is not a complete edition for `expected`; empty means it is one."""
    card = _card_part(page_html)
    text = _text(card)
    problems: list[str] = []
    if len(card) < CARD_READY_CHARS:
        problems.append(f"report body is {len(card)} chars; an edition runs to tens of thousands")

    dateline = f"{expected:%A}, {expected:%B} {expected.day}, {expected.year}"
    if dateline.lower() not in text.lower():
        problems.append(f"dateline '{dateline}' not found in the report")

    covers = re.search(r"Covers \w+day, ([A-Z][a-z]+ \d{1,2})", text)
    if not covers:
        problems.append("no 'Covers <day>'s closing prices' line")
    else:
        prior = parse_long_date(f"{covers.group(1)}, {expected.year}")
        if prior is None or not (expected - timedelta(days=5) <= prior < expected):
            problems.append(f"'Covers {covers.group(1)}' is not a session before {expected}")

    tail = text[-max(2000, len(text) // 6) :]
    if DISCLAIMER not in tail:
        problems.append("disclaimer footer missing from the end (card cut off mid-load?)")

    stated, decoded = breadth_counts(card)
    if stated is None:
        problems.append("stated outperforming/underperforming counts not found")
    if decoded is None:
        problems.append("leaders/laggards table not found")
    if stated and decoded and stated != decoded:
        problems.append(
            f"stage colours decode to {decoded[0]}/{decoded[1]}; the edition states {stated[0]}/{stated[1]}"
        )
    return problems


def date_from_filename(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.name[:10])
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------
# The store, the config, the cooldown.
# ------------------------------------------------------------------------------------------------


def store_dir() -> Path:
    from cherrypick.core import home

    return home.data_dir("market-report")


def editions_dir() -> Path:
    return store_dir() / "vendor-editions"


def load_config() -> dict:
    """{"dashboard_url", "edition_title"} from the suite config's `market_report` block. Refuses
    (exit) unless both generic keys are set: nothing about the vendor lives in the code."""
    global HEADER_RE
    from cherrypick.core import home

    path = home.config_path()
    try:
        mr = json.loads(path.read_text(encoding="utf-8")).get("market_report") or {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read the suite config at {path}: {exc}") from exc
    url, title = mr.get("vendor_dashboard_url"), mr.get("vendor_edition_title")
    named = (("vendor_dashboard_url", url), ("vendor_edition_title", title))
    missing = [key for key, value in named if not (isinstance(value, str) and value.strip())]
    if missing:
        raise SystemExit(
            "The collector is not configured: set "
            + " and ".join(missing)
            + f" in the market_report block of {path}. It does not run without them."
        )
    HEADER_RE = header_re(title)
    return {"dashboard_url": url.strip(), "edition_title": title.strip()}


def _state_path() -> Path:
    return store_dir() / "collector_state.json"


def cooldown_until() -> datetime | None:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8")).get("cooldown_until")
        return datetime.fromisoformat(raw) if raw else None
    except (OSError, ValueError):
        return None


def start_cooldown(reason: str) -> None:
    until = datetime.now(UTC) + COOLDOWN
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(
        json.dumps({"cooldown_until": until.isoformat(), "reason": reason}, indent=2), encoding="utf-8"
    )


# An expired session that the one auto-login could not renew stops every fetch until a person signs
# in. One warning at the time is easy to miss, so it repeats hourly (the `session-alert` job, which
# never contacts the vendor) until a fetch that was missed has run on a working session again.
SESSION_ALERT_EVERY = timedelta(hours=1)


def _session_alert_path() -> Path:
    return store_dir() / "session_alert.json"


def read_session_alert() -> dict | None:
    try:
        state = json.loads(_session_alert_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if state.get("pending") else None


def _write_session_alert(state: dict | None) -> None:
    path = _session_alert_path()
    if not state or not state.get("pending"):
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def mark_session_expired(fetch: str, reason: str, now: datetime | None = None) -> tuple[dict, bool]:
    """Record that `fetch` could not run for want of a session. The time first noticed is kept
    across later failures; returns (state, first) where `first` says this is a new outage."""
    now = now or datetime.now(UTC)
    state = read_session_alert()
    first = state is None
    state = state or {"noticed_at": now.isoformat(), "pending": [], "last_alert_at": None}
    if fetch not in state["pending"]:
        state["pending"].append(fetch)
    state["reason"] = reason
    _write_session_alert(state)
    return state, first


def clear_session_fetch(fetch: str) -> None:
    """`fetch` has run on a working session: it is no longer waiting. The alert ends with the last."""
    state = read_session_alert()
    if state and fetch in state["pending"]:
        state["pending"].remove(fetch)
        _write_session_alert(state)


def session_alert_due(state: dict | None, now: datetime) -> bool:
    if not state or not state.get("pending"):
        return False
    last = state.get("last_alert_at")
    return last is None or now - datetime.fromisoformat(last) >= SESSION_ALERT_EVERY


def _et(moment: datetime) -> str:
    from zoneinfo import ZoneInfo

    return f"{moment.astimezone(ZoneInfo('America/New_York')):%a %Y-%m-%d %H:%M} ET"


def session_alert_text(state: dict, now: datetime) -> tuple[str, str]:
    noticed = datetime.fromisoformat(state["noticed_at"])
    hours = (now - noticed).total_seconds() / 3600
    return (
        "Report collector: vendor session expired",
        f"Expired session noticed {_et(noticed)} ({hours:.1f} h ago); "
        f"not fetched since: {', '.join(state['pending'])}.\n"
        f"Reason: {state.get('reason') or 'unknown'}\n"
        "Run: python scripts/fetch_vendor_edition.py login -- it signs in, then runs the missed "
        "fetches. This repeats every hour until they have run.",
    )


def _session_expired(fetch: str, exc: Exception) -> None:
    """The NeedsPerson path of every fetch: record it, and warn now only if this is a new outage
    (a later failure in the same outage waits for the hourly reminder)."""
    now = datetime.now(UTC)
    state, first = mark_session_expired(fetch, str(exc), now)
    if first:
        _warn(*session_alert_text(state, now))
        state["last_alert_at"] = now.isoformat()
        _write_session_alert(state)


def cmd_session_alert(_args) -> int:
    """The hourly reminder, run by the supervisor every few minutes: offline, no browser."""
    now = datetime.now(UTC)
    state = read_session_alert()
    if not session_alert_due(state, now):
        return 0
    _warn(*session_alert_text(state, now))
    state["last_alert_at"] = now.isoformat()
    _write_session_alert(state)
    return 0


def _warn(title: str, message: str) -> None:
    """Best-effort WARNING through the orchestrator's notifier; a machine without it loses only the
    notification."""
    print(f"WARNING: {title}\n{message}", file=sys.stderr)
    try:
        from cherrypick.notify.notifier import Notifier
        from cherrypick.orchestrator import config as cfgmod

        Notifier(cfgmod.load_config().get("notify")).notify(
            "WARNING", "market_report.collector", title, message
        )
    except Exception:  # noqa: BLE001
        pass


def _snapshot(page) -> Path | str:
    """Keep what the page looked like when a run failed: a screenshot and the HTML, so the next
    fix is made from evidence instead of another visit to the vendor."""
    try:
        out = store_dir() / "failures" / f"{datetime.now():%Y%m%d-%H%M%S}"
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / "page.png"), full_page=True)
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        return out
    except Exception:  # noqa: BLE001
        return "unavailable"


def _pause(bounds: tuple[float, float] = PAUSE_RANGE_S) -> None:
    time.sleep(random.uniform(*bounds))


class Throttled(RuntimeError):
    pass


class NeedsPerson(RuntimeError):
    """A captcha, second factor or refused login: a person has to act."""


# ------------------------------------------------------------------------------------------------
# The browser.
# ------------------------------------------------------------------------------------------------


def chrome_user_agent(version: str, platform: str = sys.platform) -> str:
    """The user-agent a regular (headed) Chrome of this version sends: major version only, the
    rest zeroed, as Chrome itself reports it. Headless Chromium says "HeadlessChrome"; the user
    asked (2026-09-27) for the collector to present as Chrome instead. Only the user-agent is
    changed — `navigator.webdriver` is left as the browser sets it."""
    major = version.split(".")[0]
    os_part = {
        "win32": "Windows NT 10.0; Win64; x64",
        "darwin": "Macintosh; Intel Mac OS X 10_15_7",
    }.get(platform, "X11; Linux x86_64")
    return (
        f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def _installed_chrome_version(pw) -> str:
    """Read from the bundled browser itself (a local launch, no network), so the user-agent keeps
    matching the engine after a Playwright upgrade."""
    browser = pw.chromium.launch(headless=True)
    try:
        return browser.version
    finally:
        browser.close()


# ------------------------------------------------------------------------------------------------
# One browser profile, one process (2026-10-09). The supervisor runs one browser job at a time, but
# a person's `login`, a hand-run capture or a probe started beside a scheduled run all open the SAME
# profile -- and a second Chrome on a profile in use dies ("Target page, context or browser has been
# closed"). Every browser command of both collectors takes this lock first; a second one waits a few
# minutes, then gives up with a plain message instead of crashing Chrome. Shared with the QuikOptions
# collector (loaded from beside it), so the two cannot drift apart.
# ------------------------------------------------------------------------------------------------
PROFILE_LOCK_WAIT_S = 600
PROFILE_BUSY_EXIT = 75  # EX_TEMPFAIL: try again later


class ProfileBusy(RuntimeError):
    pass


def profile_lock_path(profile: Path) -> Path:
    return profile.with_name(profile.name + ".lock")


def profile_holder(profile: Path) -> int | None:
    try:
        return int(profile_lock_path(profile).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


class profile_lock:  # noqa: N801 -- used as `with profile_lock(...)`
    """Hold `profile`'s lock for the life of the `with`; wait up to `wait_s` for another holder."""

    def __init__(self, profile: Path, wait_s: float = PROFILE_LOCK_WAIT_S, *, sleep=None, log=None):
        self.profile, self.wait_s = Path(profile), wait_s
        self.sleep = sleep or time.sleep
        self.log = log or (lambda m: print(m, file=sys.stderr))

    def __enter__(self):
        from cherrypick.core import looplock

        path, waited = profile_lock_path(self.profile), 0.0
        # The lock is the holder's PID; a dead holder's lock is taken over at once (core.looplock).
        while not looplock.acquire(path, stale_seconds=6 * 3600):
            if waited >= self.wait_s:
                raise ProfileBusy(
                    f"the browser profile {self.profile} is in use by pid {profile_holder(self.profile)}; "
                    f"waited {waited:.0f} s. Let that run finish (or close its browser), then try again."
                )
            if waited == 0:
                self.log(f"browser profile in use by pid {profile_holder(self.profile)}; waiting")
            self.sleep(5)
            waited += 5
        return self

    def __exit__(self, *exc):
        from cherrypick.core import looplock

        looplock.release(profile_lock_path(self.profile))
        return False


def cookie_report(cookies: list[dict], host: str, now: float) -> dict:
    """What the profile's cookies say about the session on `host`, read locally: how many the site
    set, how many are persistent and unexpired, and the soonest and latest expiry. `signed_out` only
    when NO unexpired persistent cookie for the site remains -- certain; anything finer would be a
    guess about which cookie is the login. Pure."""
    host = (host or "").lower()

    def for_host(c: dict) -> bool:
        d = str(c.get("domain") or "").lstrip(".").lower()
        return bool(d) and (host == d or host.endswith("." + d))

    site = [c for c in cookies if for_host(c)]
    persistent = [c for c in site if float(c.get("expires") or -1) > 0]
    live = sorted(float(c["expires"]) for c in persistent if float(c["expires"]) > now)

    def iso(ts):
        return datetime.fromtimestamp(ts, UTC).isoformat(timespec="minutes") if ts else None

    return {
        "site_cookies": len(site),
        "persistent": len(persistent),
        "unexpired": len(live),
        "soonest_expiry": iso(live[0]) if live else None,
        "latest_expiry": iso(live[-1]) if live else None,
        "signed_out": not live,
    }


def smoke(open_browser, host: str, collector: str, warn) -> int:
    """Open the collector's browser exactly as its scheduled run does (same profile, headless), read
    the session cookies, close it. Never navigates: nothing is requested from the site and nothing is
    recorded. Exit 0 ready; 3 signed out (warned: a person must sign in before the next run); 1 the
    browser would not start (warned)."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            ctx = open_browser(pw)
            try:
                cookies = ctx.cookies()
            finally:
                ctx.close()
    except Exception as exc:  # noqa: BLE001 -- the failure IS the finding
        warn(f"{collector}: the browser would not start", f"{type(exc).__name__}: {exc}"[:500])
        print(json.dumps({"ok": False, "browser": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}))
        return 1
    report = cookie_report(cookies, host, time.time())
    print(json.dumps({"ok": not report["signed_out"], "browser": "started", **report}, indent=1))
    if report["signed_out"]:
        warn(
            f"{collector}: signed out -- sign in before the next run",
            f"No unexpired session cookie for {host} in the collector's browser profile. Run its "
            "`login` command from your desktop session.",
        )
        return 3
    return 0


def _open_browser(pw, headed: bool):
    profile = store_dir() / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(profile),
        headless=not headed,
        viewport={"width": 1400, "height": 1000},
        user_agent=chrome_user_agent(_installed_chrome_version(pw)),
    )


def _watch_for_throttling(page, hits: list[str]) -> None:
    def on_response(resp):
        if resp.status in (403, 429):
            hits.append(f"{resp.status} {resp.url}")

    page.on("response", on_response)


def _login_needed(page) -> bool:
    return page.locator("input[type=password]").count() > 0


def _open_authenticated(page, url: str, hits: list[str]) -> None:
    """Open the dashboard, logging in if the saved session has lapsed.

    A 403 from a logged-OUT page load is the vendor saying "who are you", not "slow down": on
    2026-09-30 the expired session's first /securities call came back 403, the auto-login then
    worked and loaded 12,489 symbols, and the stale 403 still in `hits` was read as throttling --
    a 24-hour cooldown, an exit 1, and the next morning's run skipped too. So what the watcher
    saw before a successful login is forgotten; a 403 on the authenticated reload still counts.
    """
    _open_dashboard(page, url)
    if _login_needed(page):
        _auto_login(page)
        hits.clear()
        _open_dashboard(page, url)


def _research_tab(page):
    """The Insights panel's Research category: a toolbar div labelled "Research", not a tab role
    (seen 2026-09-27)."""
    return page.locator(".ins-categories [aria-label='Research']")


def _open_dashboard(page, url: str) -> None:
    """Load the dashboard and wait for it to show either a login form or the Research tab.

    Never waits for network idle: the dashboard holds a live connection open for streaming quotes,
    so the network never goes quiet (the first live run timed out on exactly that)."""
    page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if _login_needed(page) or _research_tab(page).count():
            return
        time.sleep(2)
    raise RuntimeError("the dashboard showed neither a login form nor the Research tab in 90 s")


def _challenge_visible(page) -> bool:
    frames = page.locator(
        "iframe[src*='captcha'], iframe[src*='hcaptcha'], "
        "iframe[src*='turnstile'], iframe[title*='challenge' i]"
    )
    return frames.count() > 0


def _auto_login(page) -> None:
    """One attempt with the keyring credentials. Anything unexpected is a NeedsPerson."""
    from cherrypick.core.auth.credentials import CredentialStore

    store = CredentialStore(KEYRING_SERVICE)
    user, pwd = store.get_secret(USERNAME), store.get_secret(PASSWORD)
    if not (user and pwd):
        raise NeedsPerson(
            "the session has expired and no credentials are stored "
            "(run `credentials`, or `login` to sign in by hand)"
        )
    if _challenge_visible(page):
        raise NeedsPerson("the login page is showing a challenge")
    page.locator("input[type=email], input[type=text], input[name*=user i]").first.fill(user)
    page.locator("input[type=password]").first.fill(pwd)
    page.locator("input[type=password]").first.press("Enter")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and _login_needed(page) and not _challenge_visible(page):
        time.sleep(2)
    if _challenge_visible(page) or _login_needed(page):
        raise NeedsPerson("the login did not complete (challenge, second factor or refusal)")


def _research_headers(page):
    """Switch the Insights panel to Research and return its report headers.

    The dashboard renders its elements before it wires their click handlers, so a click the moment
    the tab appears does nothing (the second live run clicked and stayed on DailyPlay). Settle,
    click, and confirm the switch; the retries are clicks on the same page, never reloads."""
    tab = _research_tab(page).first
    time.sleep(5)
    for _ in range(3):
        tab.click()
        time.sleep(4)
        if tab.get_attribute("aria-selected") == "true":
            break
    else:
        raise RuntimeError("the Research tab did not become active after three clicks")
    headers = page.locator("button.ins-card-header", has_text=HEADER_RE)
    headers.first.wait_for(timeout=60_000)
    return headers


_BUILD_PAGE_JS = """
(card, title) => {
  const c = card.cloneNode(true);
  for (const b of c.querySelectorAll('button')) {
    if (b.textContent.includes(title)) { b.remove(); break; }
  }
  const links = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
      .map(l => `<link rel="stylesheet" href="${l.href}">`).join('\\n');
  const styles = Array.from(document.querySelectorAll('style')).map(s => s.outerHTML).join('\\n');
  return `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>${links}\\n`
       + `${styles}<style>body{background:#fff;color:#111;margin:24px auto;max-width:900px}</style>`
       + `</head><body><h1 style="font-family:sans-serif">${title}</h1>${c.innerHTML}</body></html>`;
}
"""


def _capture_card(page, header) -> tuple[str, date]:
    title = header.inner_text().strip()
    m = HEADER_RE.search(title)
    when = parse_long_date(m.group(1)) if m else None
    if when is None:
        raise RuntimeError(f"unreadable report header: {title!r}")
    title = m.group(0)
    card = header.locator(
        "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ins-card ')][1]"
    )
    header.click()
    deadline = time.monotonic() + CARD_READY_TIMEOUT_S
    while card.evaluate("e => e.innerHTML.length") <= CARD_READY_CHARS:
        if time.monotonic() > deadline:
            raise RuntimeError(f"{title}: content never finished loading")
        time.sleep(2)
    time.sleep(3)  # lazy content settles in more than one pass
    return card.evaluate(_BUILD_PAGE_JS, title), when


def save_edition(page_html: str, when: date, out_dir: Path) -> tuple[bool, list[str]]:
    """Write `YYYY-MM-DD.html` if it validates and does not exist yet; a failure goes to
    `.rejected.html`. Returns (saved, problems)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{when.isoformat()}.html"
    if target.exists():
        return False, [f"{target.name} already exists; not overwritten"]
    problems = validate_edition(page_html, when)
    dest = out_dir / f"{when.isoformat()}.rejected.html" if problems else target
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(page_html, encoding="utf-8")
    tmp.replace(dest)
    return not problems, problems


# ------------------------------------------------------------------------------------------------
# Commands.
# ------------------------------------------------------------------------------------------------


def cmd_credentials(_args) -> int:
    from cherrypick.core.auth.credentials import CredentialStore, prompt_and_store

    written = prompt_and_store(CredentialStore(KEYRING_SERVICE), (USERNAME, PASSWORD), prompt_fn=_prompt)
    print(f"stored: {', '.join(written) or 'nothing (blank input keeps the current value)'}")
    return 0


def _prompt(label: str) -> str:
    import getpass

    return input(label) if label.startswith(USERNAME) else getpass.getpass(label)


def cmd_login(_args) -> int:
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    with sync_playwright() as pw:
        ctx = _open_browser(pw, headed=True)
        page = ctx.new_page()
        page.goto(cfg["dashboard_url"])
        input("Sign in in the browser window, wait for the dashboard, then press Enter here... ")
        ok = not _login_needed(page)
        ctx.close()
    print("session saved" if ok else "still on the login page; nothing saved")
    if not ok:
        return 1
    return _run_missed_fetches()


def _run_missed_fetches() -> int:
    """After a sign-in, run each fetch an expired session stopped, a reading pause apart; each clears
    itself from the hourly alert when it completes. Editions backfill, so a missed morning is
    fetched too while the site still lists it."""
    state = read_session_alert()
    if not state:
        return 0
    runs = {
        "edition": lambda: cmd_edition(argparse.Namespace(backfill=MAX_BACKFILL, headed=False)),
        "charts": lambda: cmd_charts(argparse.Namespace(tickers=[], headed=False)),
        "screeners": lambda: cmd_screeners(argparse.Namespace(headed=False)),
    }
    rc = 0
    for i, fetch in enumerate(list(state["pending"])):
        if fetch not in runs:
            continue
        if i:
            _pause()
        print(f"running the missed {fetch} fetch")
        rc |= runs[fetch]()
    return rc


def cmd_edition(args) -> int:
    until = cooldown_until()
    if until and until > datetime.now(UTC):
        print(f"cooling down after a throttling response until {until:%Y-%m-%d %H:%M} UTC; skipping this run")
        return 0
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    wanted = 1 + min(max(args.backfill, 0), MAX_BACKFILL)
    out_dir = editions_dir()
    hits: list[str] = []
    saved, failed = [], []
    with sync_playwright() as pw:
        ctx = _open_browser(pw, headed=args.headed)
        page = ctx.new_page()
        _watch_for_throttling(page, hits)
        try:
            _open_authenticated(page, cfg["dashboard_url"], hits)
            headers = _research_headers(page)
            opened = 0
            for i in range(min(headers.count(), wanted)):
                if hits:
                    raise Throttled("; ".join(hits[:3]))
                header = headers.nth(i)
                m = HEADER_RE.search(header.inner_text())
                when = parse_long_date(m.group(1)) if m else None
                if when and (out_dir / f"{when.isoformat()}.html").exists():
                    print(f"{when}: already saved")
                    continue
                if opened:
                    _pause()
                page_html, when = _capture_card(page, header)
                opened += 1
                ok, problems = save_edition(page_html, when, out_dir)
                (saved if ok else failed).append((when, problems))
                print(
                    f"{when}: {'saved' if ok else 'REJECTED'} ({len(page_html):,} chars)"
                    + "".join(f"\n  - {p}" for p in problems)
                )
            if hits:
                raise Throttled("; ".join(hits[:3]))
            clear_session_fetch("edition")
        except Throttled as exc:
            start_cooldown(str(exc))
            _warn(
                "Report collector: vendor throttled or refused a request",
                f"{exc}\nNo further requests for 24 hours. Fetch today's edition by hand.",
            )
            return 1
        except NeedsPerson as exc:
            _session_expired("edition", exc)
            return 1
        except Exception as exc:  # noqa: BLE001 -- anything else is a page that changed shape
            where = _snapshot(page)
            _warn(
                "Report collector failed",
                f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0]}\n"
                f"Nothing was saved. Page snapshot: {where}\n"
                "Fetch today's edition by hand if it matters.",
            )
            return 1
        finally:
            ctx.close()
    if failed:
        _warn("Report collector rejected an edition", "\n".join(f"{d}: {'; '.join(p)}" for d, p in failed))
        return 1
    return 0


def cmd_validate(args) -> int:
    bad = 0
    for name in args.files:
        path = Path(name)
        when = date_from_filename(path)
        if when is None:
            print(f"{path.name}: name is not YYYY-MM-DD.html")
            bad += 1
            continue
        problems = validate_edition(path.read_text(encoding="utf-8"), when)
        stated, decoded = breadth_counts(_card_part(path.read_text(encoding="utf-8")))
        print(
            f"{path.name}: {'ok' if not problems else 'FAIL'} stated={stated} decoded={decoded}"
            + "".join(f"\n  - {p}" for p in problems)
        )
        bad += bool(problems)
    return 1 if bad else 0


def cmd_probe_chart(args) -> int:
    """Record one chart page — its JSON responses, text and a screenshot — for designing the
    chart-page parser. One page, one visit."""
    from playwright.sync_api import sync_playwright

    latest = sorted(editions_dir().glob("????-??-??.html"))
    if not latest:
        raise SystemExit("no saved edition to take the chart-page address from")
    href = re.search(r'href="([^"]*\?symbol=)[A-Z.]+"', latest[-1].read_text(encoding="utf-8"))
    if not href:
        raise SystemExit("no chart-page link found in the latest edition")
    url = href.group(1) + args.ticker.upper()
    out = store_dir() / "probes" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.ticker.upper()}"
    out.mkdir(parents=True, exist_ok=True)
    responses: list[dict] = []
    with sync_playwright() as pw:
        ctx = _open_browser(pw, headed=args.headed)
        page = ctx.new_page()

        def on_response(resp):
            ctype = resp.headers.get("content-type", "")
            entry = {"status": resp.status, "url": resp.url, "type": ctype}
            if "json" in ctype:
                try:
                    entry["body"] = resp.text()[:200_000]
                except Exception:  # noqa: BLE001
                    entry["body"] = None
            responses.append(entry)

        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(20)  # the page streams; give its data calls time to land, then read once
        (out / "page.txt").write_text(page.inner_text("body"), encoding="utf-8")
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "page.png"), full_page=True)
        ctx.close()
    (out / "responses.json").write_text(json.dumps(responses, indent=1), encoding="utf-8")
    throttled = [r for r in responses if r["status"] in (403, 429)]
    print(
        f"recorded {len(responses)} responses to {out}"
        + (f"\nWARNING: {len(throttled)} throttled/refused responses" if throttled else "")
    )
    return 0


# ------------------------------------------------------------------------------------------------
# Chart pages: the data behind them, not pictures of them.
# ------------------------------------------------------------------------------------------------

# The fixed half of the chart panel: the 22 names captured by hand on 2026-09-27 (they span every
# state the reports name) plus the index funds the headline read sits on. The other half is every
# name the latest edition mentions outside its big leaders/laggards table.
DEFAULT_PANEL = (
    "ANET KEYS AME ETN AMD META MSFT ISRG MGM ORCL DTE MS AVGO ARE MTN SPY QQQ IWM IGV XLI XLE TLT RSP"
).split()
MAX_CHARTS = 40
HOW_WAIT_S = 12  # how long a chart page is given to deliver its strategy response


def edition_symbols(page_html: str) -> list[str]:
    """Names the edition discusses, in order: everything linked outside the leaders/laggards table
    (164 names, the breadth screen, not a discussion) and the rotation fund list."""
    i = page_html.find(">Sector</th>")
    j = page_html.find("The three shades", i)
    a = page_html.find("Sector Rotation")
    b = page_html.find("Relative Strength Leadership", a)
    if i < 0 or j < 0:
        keep = page_html
    elif 0 <= a < b <= i:
        keep = page_html[:a] + page_html[b:i] + page_html[j:]
    else:
        keep = page_html[:i] + page_html[j:]
    return list(dict.fromkeys(re.findall(r'\?symbol=([A-Z][A-Z.]*)"', keep)))


def chart_base_url(page_html: str) -> str | None:
    m = re.search(r'href="([^"]*\?symbol=)[A-Z.]+"', page_html)
    return m.group(1) if m else None


def validate_chart_capture(why: dict, ticker: str) -> list[str]:
    """Every reason a chart capture is not usable; empty means it is."""
    problems = []
    quotes = why.get("historicalQuotes") or []
    if not quotes:
        problems.append("no daily bars")
    elif str(quotes[-1].get("symbol", "")).split(".")[0] != ticker:
        problems.append(f"bars are for {quotes[-1].get('symbol')}, not {ticker}")
    sr = why.get("supportAndResistance")
    if not isinstance(sr, dict) or not isinstance(sr.get("support"), list):
        problems.append("no support/resistance block")
    else:
        for side in ("support", "resistance"):
            for lvl in sr.get(side) or []:
                if not isinstance(lvl.get("value"), (int, float)):
                    problems.append(f"a {side} level is not a number: {lvl!r}")
    return problems


def chart_capture_notes(why: dict) -> list[str]:
    """Gaps worth recording with a capture that do not make it unusable. The vendor sends no 1-10
    rank for some names (A, AU, ITW, PYPL, UMC and XLU on 2026-09-27) while their bars, trend
    histories and levels are complete; rejecting the capture for it threw that data away every night."""
    notes = []
    if not isinstance(why.get("technicalRank"), (int, float)):
        notes.append("no 1-10 technical rank")
    return notes


def session_of(why: dict) -> str | None:
    quotes = why.get("historicalQuotes") or []
    return str(quotes[-1].get("date", ""))[:10] or None if quotes else None


def charts_dir() -> Path:
    return store_dir() / "vendor-charts"


def cmd_charts(args) -> int:
    """Capture the data behind the chart pages for the panel, one page at a time, paced."""
    until = cooldown_until()
    if until and until > datetime.now(UTC):
        print(f"cooling down until {until:%Y-%m-%d %H:%M} UTC; skipping this run")
        return 0
    from playwright.sync_api import sync_playwright

    latest = sorted(editions_dir().glob("????-??-??.html"))
    if not latest:
        raise SystemExit("no saved edition to take the chart-page address and names from")
    edition_html = latest[-1].read_text(encoding="utf-8")
    base = chart_base_url(edition_html)
    if not base:
        raise SystemExit("no chart-page link found in the latest edition")
    cfg = load_config()
    panel = [t.upper() for t in (args.tickers or cfg.get("chart_panel") or DEFAULT_PANEL)]
    if not args.tickers:
        panel += edition_symbols(edition_html)
    panel = list(dict.fromkeys(panel))[:MAX_CHARTS]

    hits: list[str] = []
    captured: dict[str, dict] = {}
    saved, failed, visited = [], [], 0
    with sync_playwright() as pw:
        ctx = _open_browser(pw, headed=args.headed)
        page = ctx.new_page()
        _watch_for_throttling(page, hits)

        def on_response(resp):
            url = resp.url
            if resp.status != 200 or "json" not in resp.headers.get("content-type", ""):
                return
            for kind in ("/why/", "/ranks/", "/how/", "/tradeIdeas"):
                if kind in url:
                    try:
                        captured[f"{kind}|{url}"] = json.loads(resp.text())
                    except Exception:  # noqa: BLE001
                        pass

        page.on("response", on_response)
        lists: dict[str, dict] = {}
        _listen_for_screeners(page, lists)
        screener_failed: list[tuple[str, list[str]]] = []
        try:
            # The evening visit starts where a person's would: the dashboard, the income screeners,
            # then the chart pages. Opening the dashboard first also renews a lapsed session before
            # any chart page is asked for; on 2026-10-06 a lapsed session's chart-page 403s were
            # read as throttling and cost a day of charts and the next morning's edition.
            _open_authenticated(page, cfg["dashboard_url"], hits)
            try:
                _browse_screeners(page, lists, hits)
            except Throttled:
                raise
            except Exception as exc:  # noqa: BLE001 -- the screeners failing must not cost the charts
                screener_failed.append(
                    ("screeners", [f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0]}"])
                )
            screener_failed += _save_screeners(lists)
            visited = 1  # the dashboard was a page: the first chart page waits its pause like the rest
            for ticker in panel:
                if hits:
                    raise Throttled("; ".join(hits[:3]))
                if visited:
                    _pause(CHART_PAUSE_RANGE_S)
                captured.clear()
                page.goto(base + ticker, wait_until="domcontentloaded", timeout=90_000)
                visited += 1
                deadline = time.monotonic() + 60
                why = ranks = None
                while time.monotonic() < deadline and why is None:
                    page.wait_for_timeout(2000)  # not time.sleep: events only arrive inside Playwright calls
                    for key, body in list(captured.items()):
                        if key.startswith("/why/") and f"/{ticker}." in key:
                            why = body
                # The strategy response (the whole option chain with the vendor's own greeks, the
                # answer key for the income screeners' strike rule) is the page's heaviest and can
                # land after the chart data; a reader is still on the page, so wait a little for it.
                how = None
                how_deadline = time.monotonic() + HOW_WAIT_S
                while how is None:
                    page.wait_for_timeout(2000)  # not time.sleep: events only arrive inside Playwright calls
                    how = next(
                        (b for k, b in captured.items() if k.startswith("/how/") and f"/{ticker}." in k), None
                    )
                    if time.monotonic() > how_deadline:
                        break
                for key, body in list(captured.items()):
                    if key.startswith("/ranks/") and f"/{ticker}." in key:
                        ranks = body
                    if key.startswith("/tradeIdeas"):
                        _save_trade_ideas(body)
                if why is None:
                    failed.append((ticker, ["the chart page never loaded its data"]))
                    print(f"{ticker}: no data")
                    continue
                problems = validate_chart_capture(why, ticker)
                session = session_of(why) or datetime.now().date().isoformat()
                out = charts_dir() / session
                out.mkdir(parents=True, exist_ok=True)
                target = out / f"{ticker}.json"
                if target.exists():
                    print(f"{ticker} {session}: already saved")
                    continue
                dest = out / f"{ticker}.rejected.json" if problems else target
                record = {
                    "ticker": ticker,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "notes": chart_capture_notes(why)
                    + ([] if how else ["no strategy response (option chain)"]),
                    "why": why,
                    "ranks": ranks,
                    "how": how,
                }
                dest.write_text(json.dumps(record), encoding="utf-8")
                (failed if problems else saved).append((ticker, problems))
                sr = why.get("supportAndResistance") or {}
                print(
                    f"{ticker} {session}: {'saved' if not problems else 'REJECTED'} "
                    f"rank={why.get('technicalRank')} support={len(sr.get('support') or [])} "
                    f"resistance={len(sr.get('resistance') or [])}" + "".join(f"\n  - {p}" for p in problems)
                )
            if hits:
                raise Throttled("; ".join(hits[:3]))
            clear_session_fetch("charts")
            if not screener_failed:
                clear_session_fetch("screeners")
        except Throttled as exc:
            start_cooldown(str(exc))
            _warn(
                "Chart collector: vendor throttled or refused a request",
                f"{exc}\nNo further requests for 24 hours.",
            )
            return 1
        except NeedsPerson as exc:
            _session_expired("charts", exc)
            return 1
        except Exception as exc:  # noqa: BLE001
            where = _snapshot(page)
            _warn(
                "Chart collector failed",
                f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0]}\n"
                f"Captured {len(saved)} before it stopped. Page snapshot: {where}",
            )
            return 1
        finally:
            ctx.close()
    print(f"{len(saved)} saved, {len(failed)} rejected, {visited} pages visited")
    if failed:
        _warn("Chart collector rejected some captures", "\n".join(f"{t}: {'; '.join(p)}" for t, p in failed))
    if screener_failed:
        _warn(
            "Chart collector missed or rejected a screener list",
            "\n".join(f"{n}: {'; '.join(p)}" for n, p in screener_failed),
        )
    return 0


# ------------------------------------------------------------------------------------------------
# Income screeners: the dashboard's Reports Explorer, three lists the app loads as JSON.
# ------------------------------------------------------------------------------------------------

# (store name, the app's own request path, the list's key in that response, the tab's label). The
# credit-spread list arrives with the dashboard itself (its default tab); the other two load when
# their tab is clicked. Each response is the WHOLE list (766 covered calls on 2026-10-07, where the
# widget shows a filtered page), with the screen's own parameters on every row.
SCREENERS = (
    ("credit-spreads", "/reports/creditspreads/all", "creditSpreads", "Credit Spreads"),
    ("covered-calls", "/reports/coveredcalls/all", "coveredCalls", "Covered Calls"),
    ("short-puts", "/reports/shortputs/all", "shortPuts", "Short Puts"),
)
SCREENER_READY_TIMEOUT_S = 45


def screener_session(body: dict) -> str | None:
    """The day a list was built, from its own `created` stamp ('10/07/2026 03:48 PM')."""
    try:
        return datetime.strptime(str(body.get("created", ""))[:10], "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def validate_screener(name: str, body: dict) -> list[str]:
    """Every reason a captured list is not usable; empty means it is."""
    key = {n: k for n, _, k, _ in SCREENERS}[name]
    rows = body.get(key)
    if not isinstance(rows, list) or not rows:
        return [f"no rows under '{key}'"]
    problems = []
    if screener_session(body) is None:
        problems.append(f"unreadable 'created' stamp: {body.get('created')!r}")
    bad = 0
    for row in rows:
        if name == "credit-spreads":
            strike = row.get("strike") or {}
            numbers = (strike.get("sell"), strike.get("buy"), (row.get("premium") or {}).get("value"))
        else:
            numbers = (row.get("strikePrice"), row.get("midPrice"))
        if not (isinstance(row.get("symbol"), str) and row.get("expiry")) or not all(
            isinstance(x, (int, float)) for x in numbers
        ):
            bad += 1
    if bad:
        problems.append(f"{bad} of {len(rows)} rows lack a symbol, an expiry or a numeric strike/premium")
    return problems


def screeners_dir() -> Path:
    return store_dir() / "vendor-screeners"


def save_screener(name: str, body: dict, out_root: Path) -> tuple[bool, list[str]]:
    """Write `<session>/<name>.json` if it validates and that day's list is not saved yet; a failure
    goes to `<name>.rejected.json`. The lists re-price through the day, so the first capture after
    the close is the day's record and a later one never replaces it. Returns (saved, problems)."""
    session = screener_session(body) or datetime.now().date().isoformat()
    out = out_root / session
    target = out / f"{name}.json"
    if target.exists():
        return False, [f"{session}/{target.name} already exists; not overwritten"]
    problems = validate_screener(name, body)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{name}.rejected.json" if problems else target
    record = {"name": name, "fetched_at": datetime.now(UTC).isoformat(), "body": body}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    tmp.replace(dest)
    return not problems, problems


def screener_of(url: str) -> str | None:
    for name, path, _, _ in SCREENERS:
        if path in url:
            return name
    return None


def _browse_screeners(page, lists: dict[str, dict], hits: list[str]) -> None:
    """On an open dashboard, look through the Reports Explorer as a person would: bring it into
    view, then open each tab whose list has not arrived yet, a reading pause apart. `lists` is
    filled by the page's response listener; nothing here makes a request of its own."""
    page.wait_for_timeout(random.uniform(8000, 14000))  # the dashboard settles before anyone reads it
    explorer = page.get_by_text("Reports Explorer", exact=True)
    if not explorer.count():
        raise RuntimeError("no Reports Explorer on the dashboard")
    explorer.first.scroll_into_view_if_needed()
    page.wait_for_timeout(random.uniform(3000, 6000))
    clicked = 0
    for name, _, _, label in SCREENERS:
        if hits:
            raise Throttled("; ".join(hits[:3]))
        if name in lists:
            continue
        if clicked:
            page.wait_for_timeout(random.uniform(*PAUSE_RANGE_S) * 1000)
        tab = page.get_by_text(label, exact=True)
        if not tab.count():
            raise RuntimeError(f"no '{label}' tab in the Reports Explorer")
        tab.first.click()
        clicked += 1
        deadline = time.monotonic() + SCREENER_READY_TIMEOUT_S
        while name not in lists and time.monotonic() < deadline:
            page.wait_for_timeout(2000)  # not time.sleep: events only arrive inside Playwright calls


def _save_screeners(lists: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """Save what arrived; return (name, problems) for every list missing or rejected."""
    failed = []
    for name, *_ in SCREENERS:
        if name not in lists:
            failed.append((name, ["the list never arrived"]))
            print(f"screener {name}: not captured")
            continue
        ok, problems = save_screener(name, lists[name], screeners_dir())
        key = {n: k for n, _, k, _ in SCREENERS}[name]
        print(
            f"screener {name} {screener_session(lists[name])}: "
            f"{'saved' if ok else 'not saved'} ({len(lists[name].get(key) or [])} rows)"
            + "".join(f"\n  - {p}" for p in problems)
        )
        if problems and not problems[0].endswith("not overwritten"):
            failed.append((name, problems))
    return failed


def _listen_for_screeners(page, lists: dict[str, dict]) -> None:
    def on_response(resp):
        name = screener_of(resp.url)
        if name and resp.status == 200 and "json" in resp.headers.get("content-type", ""):
            try:
                lists[name] = json.loads(resp.text())
            except Exception:  # noqa: BLE001
                pass

    page.on("response", on_response)


def cmd_screeners(args) -> int:
    """Capture the three income screener lists: one dashboard visit, the tabs a pause apart."""
    until = cooldown_until()
    if until and until > datetime.now(UTC):
        print(f"cooling down until {until:%Y-%m-%d %H:%M} UTC; skipping this run")
        return 0
    from playwright.sync_api import sync_playwright

    cfg = load_config()
    hits: list[str] = []
    lists: dict[str, dict] = {}
    with sync_playwright() as pw:
        ctx = _open_browser(pw, headed=args.headed)
        page = ctx.new_page()
        _watch_for_throttling(page, hits)
        _listen_for_screeners(page, lists)
        try:
            _open_authenticated(page, cfg["dashboard_url"], hits)
            _browse_screeners(page, lists, hits)
            if hits:
                raise Throttled("; ".join(hits[:3]))
            clear_session_fetch("screeners")
        except Throttled as exc:
            start_cooldown(str(exc))
            _warn(
                "Screener collector: vendor throttled or refused a request",
                f"{exc}\nNo further requests for 24 hours.",
            )
            return 1
        except NeedsPerson as exc:
            _session_expired("screeners", exc)
            return 1
        except Exception as exc:  # noqa: BLE001
            where = _snapshot(page)
            _warn(
                "Screener collector failed",
                f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0]}\nPage snapshot: {where}",
            )
        finally:
            ctx.close()
    failed = _save_screeners(lists)
    if failed:
        _warn(
            "Screener collector missed or rejected a list",
            "\n".join(f"{n}: {'; '.join(p)}" for n, p in failed),
        )
        return 1
    return 0


def _save_trade_ideas(body: dict) -> None:
    """The scanner's whole signal list for a day arrives with any chart page; keep one per scan
    date."""
    ideas = body.get("tradeIdeas") or []
    if not ideas:
        return
    day = str(ideas[0].get("dateOfScan", ""))[:10]
    if not day:
        return
    out = charts_dir() / day
    out.mkdir(parents=True, exist_ok=True)
    target = out / "trade-ideas.json"
    if not target.exists():
        target.write_text(json.dumps(body), encoding="utf-8")


def cmd_smoke(_args) -> int:
    """Is the browser able to start here, and is the session still signed in? Local only."""
    from urllib.parse import urlparse

    host = urlparse(load_config()["dashboard_url"]).hostname or ""
    return smoke(lambda pw: _open_browser(pw, headed=False), host, "market-report collector", _warn)


# The commands that open the browser profile, and so take its lock first.
BROWSER_COMMANDS = {"login", "edition", "charts", "screeners", "probe-chart", "smoke"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("smoke").set_defaults(fn=cmd_smoke)
    sub.add_parser("credentials").set_defaults(fn=cmd_credentials)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    ed = sub.add_parser("edition")
    ed.add_argument(
        "--backfill",
        type=int,
        default=0,
        help=f"also fetch up to this many earlier editions not yet saved (max {MAX_BACKFILL})",
    )
    ed.add_argument("--headed", action="store_true")
    ed.set_defaults(fn=cmd_edition)
    va = sub.add_parser("validate")
    va.add_argument("files", nargs="+")
    va.set_defaults(fn=cmd_validate)
    ch = sub.add_parser("charts")
    ch.add_argument("tickers", nargs="*", help="override the panel (default: fixed + edition names)")
    ch.add_argument("--headed", action="store_true")
    ch.set_defaults(fn=cmd_charts)
    sub.add_parser("session-alert").set_defaults(fn=cmd_session_alert)
    sc = sub.add_parser("screeners")
    sc.add_argument("--headed", action="store_true")
    sc.set_defaults(fn=cmd_screeners)
    pr = sub.add_parser("probe-chart")
    pr.add_argument("ticker")
    pr.add_argument("--headed", action="store_true")
    pr.set_defaults(fn=cmd_probe_chart)
    args = ap.parse_args(argv)
    if args.cmd not in BROWSER_COMMANDS:
        return args.fn(args)
    try:
        with profile_lock(store_dir() / "browser-profile"):
            return args.fn(args)
    except ProfileBusy as exc:
        print(f"not run: {exc}", file=sys.stderr)
        return PROFILE_BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
