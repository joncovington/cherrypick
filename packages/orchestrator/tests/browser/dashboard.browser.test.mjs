// Headless-browser test for the orchestrator dashboard's client-side behaviour:
// drag-to-reorder (sections / embeds groups, with localStorage persistence + reset) and
// collapsible sections (per-section defaults, toggle, persistence).
//
// Self-contained: it renders the page from a fixture model via render_fixture.py, serves that
// HTML from a throwaway localhost server, and drives the system Chrome through puppeteer-core.
// No live orchestrator server, config, or paper databases are needed.
//
// Skips cleanly (exit 0) when the browser, python, or puppeteer-core is unavailable, so it never
// breaks a machine that can't run it. Run it with:  npm install && npm test
// (optionally set CHROME_PATH to your Chrome/Chromium binary).

import { execFileSync } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function skip(msg) {
  console.log("SKIP — " + msg);
  process.exit(0);
}

// ── locate a Chrome/Chromium binary ──────────────────────────────────────────
function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ].filter(Boolean);
  return candidates.find((p) => {
    try { return fs.existsSync(p); } catch { return false; }
  });
}

function findPython() {
  for (const py of ["python", "python3"]) {
    try { execFileSync(py, ["--version"], { stdio: "ignore" }); return py; } catch { /* next */ }
  }
  return null;
}

// ── tiny assertion harness ───────────────────────────────────────────────────
let failures = 0;
function ok(cond, msg) {
  console.log((cond ? "PASS" : "FAIL") + " — " + msg);
  if (!cond) failures++;
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function main() {
  const chrome = findChrome();
  if (!chrome) skip("no Chrome/Chromium found (set CHROME_PATH to enable)");
  const python = findPython();
  if (!python) skip("python not found (needed to render the fixture page)");

  let puppeteer;
  try {
    puppeteer = (await import("puppeteer-core")).default;
  } catch {
    skip("puppeteer-core not installed (run `npm install` in this folder)");
  }

  // Render the dashboard HTML from the fixture model.
  let pageHtml;
  try {
    pageHtml = execFileSync(python, [path.join(HERE, "render_fixture.py")], {
      encoding: "utf-8",
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
      maxBuffer: 8 * 1024 * 1024,
    });
  } catch (e) {
    skip("could not render fixture page: " + (e.message || e));
  }
  if (!pageHtml || pageHtml.indexOf("embed-grid") < 0) skip("fixture render looked wrong");

  // Serve it from a throwaway localhost server so localStorage has a real http origin.
  const server = http.createServer((req, res) => {
    if (req.url === "/" || req.url.startsWith("/index")) {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(pageHtml);
    } else {
      res.writeHead(404); res.end("nope"); // iframe /embed/* URLs 404 harmlessly
    }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = "http://127.0.0.1:" + server.address().port + "/";

  const browser = await puppeteer.launch({
    executablePath: chrome, headless: "new", args: ["--no-sandbox", "--disable-gpu"],
  });
  const errors = [];
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(base, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => localStorage.clear());
    await page.reload({ waitUntil: "domcontentloaded" });
    await new Promise((r) => setTimeout(r, 300));

    // ── collapsible sections: defaults ──────────────────────────────────────
    const collapse = await page.evaluate(() => {
      const by = {};
      document.querySelectorAll(".card.collapsible").forEach((c) => {
        by[c.getAttribute("data-section")] = c.classList.contains("collapsed");
      });
      return by;
    });
    ok(collapse.system === true, "System collapsed by default");
    ok(collapse.meic === false, "meic embed expanded by default");
    ok(collapse.earnings === false, "earnings embed expanded by default");
    ok(collapse["gex-dashboard"] === true, "gex embed collapsed by default");
    ok(collapse["end-of-day"] === false && collapse["recent-logs"] === false,
      "EOD and logs expanded by default");

    // toggle System open, reload, confirm the state persisted
    await page.evaluate(() => {
      const sys = [...document.querySelectorAll(".card.collapsible")]
        .find((c) => c.getAttribute("data-section") === "system");
      sys.querySelector(":scope > h2").click();
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await new Promise((r) => setTimeout(r, 300));
    const sysAfter = await page.evaluate(() => [...document.querySelectorAll(".card.collapsible")]
      .find((c) => c.getAttribute("data-section") === "system").classList.contains("collapsed"));
    ok(sysAfter === false, "collapse state persists across reload (System stayed expanded)");

    // ── drag-to-reorder: sections group (must not land past the footer) ──────
    await page.evaluate(() => localStorage.removeItem("cherrypick-dash-layout-v1"));
    const secDrag = await page.evaluate(() => {
      const w = document.querySelector(".wrap");
      const kids = () => [...w.children].filter((c) => c.hasAttribute("data-rkey"));
      const before = kids().map((c) => c.getAttribute("data-rkey"));
      const card = kids()[0]; // system
      const h = card.querySelector(":scope > .reorder-handle");
      const dt = new DataTransfer();
      const r = w.getBoundingClientRect();
      h.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: dt }));
      w.dispatchEvent(new DragEvent("dragover", { bubbles: true, dataTransfer: dt, clientX: r.left + 40, clientY: r.bottom + 600 }));
      h.dispatchEvent(new DragEvent("dragend", { bubbles: true, dataTransfer: dt }));
      const footer = w.lastElementChild;
      let saved = null; try { saved = JSON.parse(localStorage.getItem("cherrypick-dash-layout-v1")); } catch { /* */ }
      return { before, after: kids().map((c) => c.getAttribute("data-rkey")),
        footerIsLast: footer && footer.className.indexOf("meta") >= 0, saved };
    });
    ok(!eq(secDrag.before, secDrag.after), "section reordered (" + secDrag.before + " -> " + secDrag.after + ")");
    ok(secDrag.footerIsLast, "dragged section did NOT land past the footer");
    ok(secDrag.saved && secDrag.saved.sections && eq(secDrag.saved.sections, secDrag.after),
      "section order saved to localStorage");

    // persists across reload
    await page.reload({ waitUntil: "domcontentloaded" });
    await new Promise((r) => setTimeout(r, 300));
    const secPersist = await page.evaluate(() => [...document.querySelector(".wrap").children]
      .filter((c) => c.hasAttribute("data-rkey")).map((c) => c.getAttribute("data-rkey")));
    ok(eq(secPersist, secDrag.after), "section order survived a reload");

    // ── drag-to-reorder: embeds group (drop-only) ───────────────────────────
    const embDrag = await page.evaluate(() => {
      const g = document.querySelector(".embed-grid");
      const kids = () => [...g.children].filter((c) => c.hasAttribute("data-rkey"));
      const before = kids().map((c) => c.getAttribute("data-rkey"));
      const card = kids()[0];
      const h = card.querySelector(".reorder-handle");
      const dt = new DataTransfer();
      const r = g.getBoundingClientRect();
      h.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer: dt }));
      g.dispatchEvent(new DragEvent("dragover", { bubbles: true, dataTransfer: dt, clientX: r.right - 5, clientY: r.bottom + 400 }));
      const midDrag = kids().map((c) => c.getAttribute("data-rkey")); // drop-only: unchanged mid-drag
      h.dispatchEvent(new DragEvent("dragend", { bubbles: true, dataTransfer: dt }));
      return { before, midDrag, after: kids().map((c) => c.getAttribute("data-rkey")) };
    });
    ok(eq(embDrag.midDrag, embDrag.before), "embeds do not reorder live during dragover (no iframe thrash)");
    ok(!eq(embDrag.before, embDrag.after), "embed reordered on drop (" + embDrag.before + " -> " + embDrag.after + ")");

    // ── reset layout restores original order + clears storage ───────────────
    const reset = await page.evaluate(() => {
      const btn = document.getElementById("reset-layout");
      const had = !!localStorage.getItem("cherrypick-dash-layout-v1");
      btn.click();
      const secOrder = [...document.querySelector(".wrap").children]
        .filter((c) => c.hasAttribute("data-rkey")).map((c) => c.getAttribute("data-rkey"));
      return { had, secOrder, cleared: !localStorage.getItem("cherrypick-dash-layout-v1") };
    });
    ok(reset.had && reset.cleared, "reset layout clears saved order from localStorage");
    ok(reset.secOrder[0] === "system", "reset restored the original section order");

    ok(errors.length === 0, "no page errors" + (errors.length ? " — " + errors.join(" | ") : ""));
  } finally {
    await browser.close();
    server.close();
  }

  console.log(failures === 0 ? "\nALL PASSED" : "\n" + failures + " FAILED");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
