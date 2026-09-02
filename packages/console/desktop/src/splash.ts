/**
 * What the window shows when the console is not answering.
 *
 * A browser error page tells you a connection was refused; this tells you which of the four things
 * went wrong and the one command that fixes it. Self-contained by necessity — it is loaded as a data
 * URL, so there is nothing to fetch and no origin to fetch it from.
 */
import type { ConsoleStatus } from "./status.js";

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function splashHtml(status: ConsoleStatus, url: string): string {
  const starting = status.state === "starting";
  // Palette lifted from the console's own styles.css so the shell does not flash a different app.
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>cherrypick console</title><style>
  :root { --bg:#0b0c0f; --bg-card:#101216; --border:#23262d; --text:#eceff3; --muted:#a6adb8; --accent:#d23f57; }
  * { box-sizing: border-box; }
  body { margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
         background:var(--bg); color:var(--text);
         font:14px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
  .card { max-width:620px; padding:32px 36px; background:var(--bg-card);
          border:1px solid var(--border); border-radius:10px; }
  h1 { margin:0 0 4px; font-size:16px; font-weight:600; letter-spacing:.01em; }
  .url { color:var(--muted); font:12px ui-monospace,SFMono-Regular,Consolas,monospace; margin-bottom:20px; }
  p { margin:0 0 18px; }
  .fix-label { color:var(--muted); font-size:12px; text-transform:uppercase;
               letter-spacing:.08em; margin-bottom:6px; }
  code { display:block; padding:10px 12px; background:#0b0c0f; border:1px solid var(--border);
         border-radius:6px; font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;
         color:var(--text); overflow-x:auto; white-space:pre; }
  .row { display:flex; align-items:center; gap:12px; margin-top:24px; }
  button { font:13px inherit; padding:7px 16px; border-radius:6px; cursor:pointer;
           border:1px solid var(--accent); background:var(--accent); color:#fff; }
  button:hover { filter:brightness(1.08); }
  .note { color:var(--muted); font-size:12px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px;
         background:${starting ? "#d9a13b" : "var(--accent)"};
         ${starting ? "animation:pulse 1.4s ease-in-out infinite;" : ""} }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.35} }
</style></head><body>
  <div class="card">
    <h1><span class="dot"></span>${starting ? "Waiting for the console" : "The console is not running"}</h1>
    <div class="url">${escapeHtml(url)}</div>
    <p>${escapeHtml(status.headline)}</p>
    ${status.fix ? `<div class="fix-label">Try this</div><code>${escapeHtml(status.fix)}</code>` : ""}
    <div class="row">
      <button onclick="location.reload()">Retry now</button>
      <span class="note">Retrying automatically${starting ? "" : " every 5s"}. The supervisor owns this
      process &mdash; this window never starts one itself.</span>
    </div>
  </div>
</body></html>`;
}

export function splashDataUrl(status: ConsoleStatus, url: string): string {
  return "data:text/html;charset=utf-8," + encodeURIComponent(splashHtml(status, url));
}
