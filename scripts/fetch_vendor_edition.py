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

The vendor's address is not in this file. It lives in `collector.json` in the store:

    {"dashboard_url": "https://..."}

Credentials live only in the OS keyring (service `cherrypick-vendor-report`).

    python scripts/fetch_vendor_edition.py credentials       # store username/password once
    python scripts/fetch_vendor_edition.py login             # sign in by hand in a visible browser
    python scripts/fetch_vendor_edition.py edition [--backfill N] [--headed]
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

HEADER_RE = re.compile(r"Vendor Report - ([A-Z][a-z]+ \d{1,2}, \d{4})")
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
    path = store_dir() / "collector.json"
    if not path.exists():
        raise SystemExit(
            f"No collector config at {path}. Create it with the dashboard address:\n"
            '    {"dashboard_url": "https://..."}'
        )
    return json.loads(path.read_text(encoding="utf-8"))


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
    return 0 if ok else 1


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
        except Throttled as exc:
            start_cooldown(str(exc))
            _warn(
                "Report collector: vendor throttled or refused a request",
                f"{exc}\nNo further requests for 24 hours. Fetch today's edition by hand.",
            )
            return 1
        except NeedsPerson as exc:
            _warn(
                "Report collector needs a person",
                f"{exc}.\nRun: python scripts/fetch_vendor_edition.py login",
            )
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
            for kind in ("/why/", "/ranks/", "/tradeIdeas"):
                if kind in url:
                    try:
                        captured[f"{kind}|{url}"] = json.loads(resp.text())
                    except Exception:  # noqa: BLE001
                        pass

        page.on("response", on_response)
        try:
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
                page.wait_for_timeout(2000)  # not time.sleep: events only arrive inside Playwright calls
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
                    "notes": chart_capture_notes(why),
                    "why": why,
                    "ranks": ranks,
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
        except Throttled as exc:
            start_cooldown(str(exc))
            _warn(
                "Chart collector: vendor throttled or refused a request",
                f"{exc}\nNo further requests for 24 hours.",
            )
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    pr = sub.add_parser("probe-chart")
    pr.add_argument("ticker")
    pr.add_argument("--headed", action="store_true")
    pr.set_defaults(fn=cmd_probe_chart)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
