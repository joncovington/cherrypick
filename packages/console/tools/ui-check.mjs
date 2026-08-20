#!/usr/bin/env node
/**
 * Drive the running console in a real browser and report what it actually rendered.
 *
 * The suite's own rule is that a front-end change is confirmed in a browser and not by tests alone,
 * and nothing here could do that: the server tests assert payloads, the web tests render components
 * in isolation, and both pass happily on a page that has never been drawn. Headless Chrome's CLI
 * (`--dump-dom`, `--screenshot`) closes most of that gap on its own, which is worth knowing when
 * this script is unavailable — but it cannot click, so anything behind a tab held in component
 * state is unreachable, and it reports no console errors. This does both.
 *
 * Uses puppeteer-CORE against the browser already on the machine: no bundled Chromium download, no
 * native module, nothing for the desktop package's electron-rebuild rule to trip over. It skips
 * cleanly when no browser is present so a checkout that cannot run it is never broken by it.
 *
 * The port comes from `@console/shared`, the same resolver the server and the Electron shell use,
 * so this cannot end up checking a different port than the one being served.
 *
 *   pnpm ui-check --route /flies --expect "loop live"
 *   pnpm ui-check --route /earnings --click overview --expect "across both books"
 *   pnpm ui-check --route /meic --shot meic.png --full
 *
 * Exit codes: 0 pass (or skipped), 1 an --expect was missing, 2 the console was unreachable.
 */

import fs from "node:fs";
import path from "node:path";
import { consoleUrl } from "@console/shared";

const SKIP = 0;
const FAIL_EXPECT = 1;
const FAIL_UNREACHABLE = 2;

/** Where a system browser lives, per platform. `CHROME_PATH` wins so an unusual install still works. */
const CANDIDATES = {
  win32: [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  ],
  darwin: [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ],
  linux: ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"],
};

function findBrowser() {
  if (process.env["CHROME_PATH"]) {
    return fs.existsSync(process.env["CHROME_PATH"]) ? process.env["CHROME_PATH"] : null;
  }
  for (const p of CANDIDATES[process.platform] ?? []) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function parseArgs(argv) {
  const out = { route: "/", clicks: [], expects: [], waits: [], viewport: "1500x1000", timeout: 20000 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === "--route") out.route = next();
    else if (a === "--click") out.clicks.push(next());
    else if (a === "--expect") out.expects.push(next());
    else if (a === "--wait") out.waits.push(next());
    else if (a === "--shot") out.shot = next();
    else if (a === "--dump") out.dump = next();
    else if (a === "--full") out.full = true;
    else if (a === "--viewport") out.viewport = next();
    else if (a === "--timeout") out.timeout = Number(next());
    else if (a === "--help" || a === "-h") out.help = true;
    else throw new Error(`unknown argument: ${a}`);
  }
  return out;
}

/**
 * Click the first visible control whose trimmed text matches — the tabs are plain buttons carrying
 * component state, so there is no URL to navigate to and no stable id to select on.
 */
async function clickByText(page, text) {
  const result = await page.evaluate((wanted) => {
    const want = wanted.trim().toLowerCase();
    // The sidebar is excluded and tabs are tried first, because several tab labels collide exactly
    // with a nav destination — "overview" is both the Earnings tab and the suite's own page. A flat
    // search over every button and link clicked the nav link, navigated away, and then reported the
    // tab's content missing, which looks like a broken page rather than a broken selector.
    const inNav = (el) => el.closest('nav, .nav, [role="navigation"]') !== null;
    for (const selector of ['[role="tab"]', ".mode-btn", "button", "a"]) {
      const nodes = [...document.querySelectorAll(selector)].filter((n) => !inNav(n));
      const label = (n) => (n.textContent ?? "").trim().toLowerCase();
      const hit = nodes.find((n) => label(n) === want) ?? nodes.find((n) => label(n).includes(want));
      if (hit !== undefined) {
        hit.click();
        return { ok: true, via: selector, label: (hit.textContent ?? "").trim() };
      }
    }
    return { ok: false };
  }, text);
  if (result.ok !== true) throw new Error(`no clickable control matching "${text}" outside the nav`);
  console.log(`  clicked ${JSON.stringify(result.label)} (${result.via})`);
  // Tab content is fetched, so settle rather than assume the click was enough.
  await new Promise((r) => setTimeout(r, 1500));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(fs.readFileSync(new URL(import.meta.url), "utf-8").split("*/")[0]);
    return SKIP;
  }

  const browserPath = findBrowser();
  if (browserPath === null) {
    console.log("ui-check: no Chrome or Edge found — skipping (set CHROME_PATH to override).");
    return SKIP;
  }

  let puppeteer;
  try {
    puppeteer = (await import("puppeteer-core")).default;
  } catch {
    console.log("ui-check: puppeteer-core is not installed — skipping (`pnpm install`).");
    return SKIP;
  }

  const base = consoleUrl();
  // Git Bash rewrites a leading-slash argument into a Windows path before this process sees it, so
  // `--route /flies` arrives as `C:/Program Files/Git/flies` and fails as a missing FILE, which
  // says nothing about the real problem. Name it instead of letting the browser report it.
  if (/^[A-Za-z]:[\\/]/.test(args.route) || args.route.includes("\\")) {
    console.error(`ui-check: --route looks like a filesystem path (${args.route}).`);
    console.error("  Git Bash rewrote the leading slash. Use --route without it, or prefix the");
    console.error("  command with MSYS_NO_PATHCONV=1.");
    return FAIL_EXPECT;
  }
  const url = new URL(args.route.replace(/^\/+/, ""), base).toString();

  // A console that is not up is a real failure: the caller asked for this page specifically.
  try {
    const probe = await fetch(new URL("/api/status", base), { signal: AbortSignal.timeout(4000) });
    if (!probe.ok) throw new Error(`HTTP ${probe.status}`);
  } catch (err) {
    console.error(`ui-check: console not reachable at ${base} (${err.message}).`);
    console.error("  start it with: python run.py dashboard --serve");
    return FAIL_UNREACHABLE;
  }

  const [width, height] = args.viewport.split("x").map(Number);
  const browser = await puppeteer.launch({
    executablePath: browserPath,
    headless: true,
    args: ["--disable-gpu", "--no-sandbox", "--hide-scrollbars"],
    defaultViewport: { width, height },
  });

  const pageErrors = [];
  const failedRequests = [];
  try {
    const page = await browser.newPage();
    // The gap the CLI leaves: a page that renders while logging errors looks identical to one that
    // does not, and a request that 500s is invisible in the DOM.
    page.on("console", (m) => {
      if (m.type() === "error") pageErrors.push(m.text());
    });
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    page.on("requestfailed", (r) => failedRequests.push(`${r.url()} — ${r.failure()?.errorText}`));
    page.on("response", (r) => {
      if (r.status() >= 400) failedRequests.push(`${r.url()} — HTTP ${r.status()}`);
    });

    await page.goto(url, { waitUntil: "networkidle2", timeout: args.timeout });
    for (const text of args.clicks) await clickByText(page, text);
    for (const sel of args.waits) await page.waitForSelector(sel, { timeout: args.timeout });

    // Both, because neither alone is right. `innerText` is what a person can read, but it
    // approximates RENDERED text: this app scrolls an inner container rather than the body
    // (scrollHeight === clientHeight), so everything below the fold is clipped out of it and an
    // assertion against a card further down the page reports MISSING while the card is plainly
    // there. `textContent` is layout-independent and sees it, at the cost of also seeing text in
    // hidden elements. Matching either keeps a real absence detectable without inventing one.
    const body = await page.evaluate(
      () => `${document.body.innerText}\n${document.body.textContent}`,
    );
    const html = await page.content();

    if (args.dump !== undefined) {
      fs.mkdirSync(path.dirname(path.resolve(args.dump)), { recursive: true });
      fs.writeFileSync(args.dump, html, "utf-8");
      console.log(`ui-check: dumped ${html.length} bytes -> ${args.dump}`);
    }
    if (args.shot !== undefined) {
      fs.mkdirSync(path.dirname(path.resolve(args.shot)), { recursive: true });
      await page.screenshot({ path: args.shot, fullPage: args.full === true });
      console.log(`ui-check: screenshot -> ${args.shot}${args.full === true ? " (full page)" : ""}`);
    }

    const missing = args.expects.filter((t) => !body.includes(t));
    for (const t of args.expects) {
      console.log(`  ${missing.includes(t) ? "MISSING" : "found  "}  ${JSON.stringify(t)}`);
    }

    if (pageErrors.length > 0) {
      console.log(`ui-check: ${pageErrors.length} console error(s):`);
      for (const e of pageErrors.slice(0, 10)) console.log(`    ${e}`);
    }
    if (failedRequests.length > 0) {
      console.log(`ui-check: ${failedRequests.length} failed request(s):`);
      for (const r of failedRequests.slice(0, 10)) console.log(`    ${r}`);
    }

    if (missing.length > 0) {
      console.error(`ui-check: ${missing.length} expectation(s) not on the rendered page.`);
      return FAIL_EXPECT;
    }
    console.log(`ui-check: ${url} OK`);
    return SKIP;
  } finally {
    await browser.close();
  }
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(`ui-check: ${err.message}`);
    process.exit(FAIL_EXPECT);
  },
);
