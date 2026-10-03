"""Read QuikOptions' Hot Options Report and its Resources > Calendars page with our own login.

**Why this exists.** `docs/quikoptions-plan.md`: the report adds what OCC's cleared volume cannot
(trade counts by size, the day's largest outrights, sweeps and spreads, volume against open
interest) for the console and a daily Discord series, and the site's calendar is an independent
check on `cherrypick.core.events`. A check, never a source.

A script rather than package code: it reaches the network, and nothing on a decision path may. It
writes only its own store, `~/.cherrypick/data/quikoptions/`.

**A person's pace.** The installed Chrome on a persistent profile a person signed in to by hand,
one session per run, pages reached through the menu, a dwell and a slow scroll. Data is read from
the responses the page itself receives; no request is sent that the page would not send. No
stealth plugins, no fingerprint changes, no captcha solving.

**It stops rather than fights.** A 429 ends the run at once. It never signs in by itself yet; an
expired session means a person runs `login` again.

So far this is Phase 0 of the plan — finding out what the site sends before anything parses it:

    python scripts/fetch_quikoptions.py login                # sign in by hand; the session is kept
    python scripts/fetch_quikoptions.py probe [--linger 120] # record both pages, a person present
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

SITE = "https://app.quikoptions.com"
REPORT_URL = f"{SITE}/Market/Options/THOR/Stock"
REPORT_MARKER = "Hot Options Report"
SIGN_IN_TIMEOUT_S = 600

# Pacing, as the vendor collector's: a person reading, not a crawler.
PAUSE_RANGE_S = (20.0, 45.0)
SCROLL_STEP_PX = (350, 650)
SCROLL_PAUSE_S = (1.2, 3.0)

# Bodies worth keeping; images, fonts and stylesheets are recorded by URL only.
BODY_TYPES = ("json", "text/plain", "text/html", "javascript")
BODY_RESOURCE_TYPES = ("xhr", "fetch", "document", "eventsource", "other")
MAX_BODY = 2_000_000


def store_dir() -> Path:
    from cherrypick.core import home

    return home.data_dir("quikoptions")


def _log(line: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {line}", flush=True)


def _pause(bounds: tuple[float, float] = PAUSE_RANGE_S) -> None:
    time.sleep(random.uniform(*bounds))


class Throttled(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------
# The browser.
# ------------------------------------------------------------------------------------------------


def _open_browser(pw, headed: bool = True):
    """The installed Chrome, not Playwright's bundled Chromium, on the store's own profile — so the
    site sees the same browser a person uses, and the session a person left behind."""
    profile = store_dir() / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(profile),
        channel="chrome",
        headless=not headed,
        no_viewport=True,
        args=["--start-maximized"],
    )


def _sign_in_showing(page) -> bool:
    return page.locator("input[type=password]").count() > 0


def _report_showing(page) -> bool:
    return page.get_by_text(REPORT_MARKER, exact=False).count() > 0


def _wait_for_report(page, timeout_s: float) -> bool:
    """Wait for a person to sign in (if the site asks) until the report is on screen."""
    deadline = time.monotonic() + timeout_s
    asked = False
    while time.monotonic() < deadline:
        if _report_showing(page) and not _sign_in_showing(page):
            return True
        if not asked and _sign_in_showing(page):
            _log("sign in in the browser window; waiting up to 10 minutes")
            asked = True
        time.sleep(2)
    return False


def _goto_report(page) -> bool:
    page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=90_000)
    if not _wait_for_report(page, SIGN_IN_TIMEOUT_S):
        return False
    # The site may land a fresh sign-in somewhere else; come back the way a person would.
    if "/THOR/" not in page.url:
        page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=90_000)
        return _wait_for_report(page, 90)
    return True


def _slow_scroll(page, to_bottom: bool = True) -> None:
    height = page.evaluate("document.documentElement.scrollHeight")
    pos = page.evaluate("window.scrollY")
    while (pos < height - 900) if to_bottom else (pos > 0):
        step = random.randint(*SCROLL_STEP_PX)
        page.mouse.wheel(0, step if to_bottom else -step)
        time.sleep(random.uniform(*SCROLL_PAUSE_S))
        pos = page.evaluate("window.scrollY")
        height = page.evaluate("document.documentElement.scrollHeight")


def _click_text(page, text: str, *, exact: bool = True) -> bool:
    """Click the first VISIBLE element with this text. The site renders a hidden copy of its menu
    (the 2026-10-02 probe timed out on one), so the first match in the DOM is not the one to use."""
    target = page.get_by_text(text, exact=exact).filter(visible=True).first
    if not target.count():
        _log(f"no visible '{text}' on the page")
        return False
    target.scroll_into_view_if_needed()
    target.hover()
    time.sleep(random.uniform(0.4, 1.2))
    target.click()
    return True


# ------------------------------------------------------------------------------------------------
# The recorder.
# ------------------------------------------------------------------------------------------------


class Recorder:
    """Every response and websocket frame the page receives, one JSON line each, tagged with the
    step the probe was on. Request headers are never written, so no cookie or token reaches disk
    beyond the browser profile itself."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.step = "start"
        self.throttled: list[str] = []
        self.binary_frames: dict[str, int] = {}
        self._fh = (out / "responses.jsonl").open("a", encoding="utf-8")

    def _write(self, entry: dict) -> None:
        entry = {"t": datetime.now().isoformat(timespec="milliseconds"), "step": self.step, **entry}
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()

    def on_response(self, resp) -> None:
        ctype = resp.headers.get("content-type", "")
        req = resp.request
        entry = {
            "kind": "response",
            "status": resp.status,
            "method": req.method,
            "resource": req.resource_type,
            "url": resp.url,
            "type": ctype,
        }
        if req.method != "GET" and req.post_data:
            entry["post"] = req.post_data[:20_000]
        if req.resource_type in BODY_RESOURCE_TYPES and any(t in ctype for t in BODY_TYPES):
            try:
                body = resp.text()
                entry["bytes"] = len(body)
                entry["body"] = body[:MAX_BODY]
            except Exception as exc:  # noqa: BLE001 - a redirect or an aborted body
                entry["body_error"] = str(exc)[:200]
        if resp.status == 429:
            self.throttled.append(resp.url)
        self._write(entry)

    def on_websocket(self, ws) -> None:
        self._write({"kind": "ws-open", "url": ws.url})

        def frame(direction: str):
            def handler(payload) -> None:
                # The site's frames are Blazor's binary render batches: counted, not kept (the
                # 2026-10-02 probe wrote 3,400 lines of "<n bytes>"). Text frames are kept.
                if isinstance(payload, str):
                    self._write({"kind": f"ws-{direction}", "url": ws.url, "body": payload[:MAX_BODY]})
                else:
                    self.binary_frames[direction] = self.binary_frames.get(direction, 0) + 1

            return handler

        ws.on("framereceived", frame("in"))
        ws.on("framesent", frame("out"))
        ws.on("close", lambda _ws: self._write({"kind": "ws-close", "url": ws.url}))

    def snapshot(self, page, name: str) -> None:
        (self.out / f"{name}.txt").write_text(page.inner_text("body"), encoding="utf-8")
        (self.out / f"{name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(self.out / f"{name}.png"), full_page=True)
        self._write({"kind": "snapshot", "name": name, "url": page.url})

    def check(self) -> None:
        if self.throttled:
            raise Throttled(f"429 from {self.throttled[0]}")

    def close(self) -> None:
        self._write({"kind": "ws-binary-frames", "counts": self.binary_frames})
        self._fh.close()


# ------------------------------------------------------------------------------------------------
# Commands.
# ------------------------------------------------------------------------------------------------


def cmd_login(_args) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = _open_browser(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ok = _goto_report(page)
        ctx.close()
    print("session saved" if ok else "the report never showed; sign in again")
    return 0 if ok else 1


def cmd_probe(args) -> int:
    """Record the report and the calendars page — every response, the text, the HTML and a
    screenshot of each — for designing the parsers. One visit to each, a person present."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    out = store_dir() / "probe" / f"{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(out)
    _log(f"recording to {out}")
    try:
        with sync_playwright() as pw:
            ctx = _open_browser(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", rec.on_response)
            page.on("websocket", rec.on_websocket)
            try:
                rec.step = "report-load"
                if not _goto_report(page):
                    _log("the report never showed (not signed in?); nothing more recorded")
                    return 1
                time.sleep(random.uniform(12, 20))
                rec.check()
                rec.snapshot(page, "report-top")

                rec.step = "report-scroll"
                _slow_scroll(page)
                time.sleep(random.uniform(5, 10))
                rec.check()
                rec.snapshot(page, "report-scrolled")

                # The rest is best effort: a person may be clicking around the site during a
                # probe (2026-10-02), and a miss here must not cost the linger that follows.
                try:
                    rec.step = "birdseye-toggles"
                    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
                    time.sleep(random.uniform(2, 4))
                    for label in ("VOLUME", "PREMIUM", "TRADES"):
                        rec.step = f"birdseye-{label.lower()}"
                        if _click_text(page, label):
                            time.sleep(random.uniform(5, 10))
                            rec.check()
                            if label != "TRADES":
                                rec.snapshot(page, f"birdseye-{label.lower()}")
                except PlaywrightError as exc:
                    _log(f"{rec.step} missed: {str(exc).splitlines()[0]}")

                rec.step = "pause"
                _pause()

                try:
                    rec.step = "calendars-open"
                    if _click_text(page, "RESOURCES"):
                        time.sleep(random.uniform(1.0, 2.5))
                        if _click_text(page, "Calendars", exact=False):
                            page.wait_for_load_state("domcontentloaded")
                            time.sleep(random.uniform(12, 20))
                            rec.check()
                            rec.snapshot(page, "calendars")
                        else:
                            rec.snapshot(page, "resources-menu")
                except PlaywrightError as exc:
                    _log(f"{rec.step} missed: {str(exc).splitlines()[0]}")

                if args.linger > 0:
                    rec.step = "linger"
                    _log(
                        f"recording for {args.linger} s more: click through the calendar tabs "
                        "(and anything else worth seeing); every response is kept"
                    )
                    end = time.monotonic() + args.linger
                    while time.monotonic() < end:
                        time.sleep(2)
                        rec.check()
                    rec.snapshot(page, "linger-end")
            finally:
                ctx.close()
    except Throttled as exc:
        _log(f"STOPPED: {exc}. Nothing more is requested today.")
        return 2
    finally:
        rec.close()
    _log(f"done: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    pr = sub.add_parser("probe")
    pr.add_argument("--linger", type=int, default=120, help="seconds to keep recording at the end")
    pr.set_defaults(fn=cmd_probe)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
