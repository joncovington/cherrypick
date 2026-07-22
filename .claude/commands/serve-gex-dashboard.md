---
description: Start the gex live GEX dashboard (dashboard --serve); no-op if gex isn't installed
argument-hint: [port]
---

Start the **gex** module's live GEX dashboard (`python packages/gex/run.py dashboard --serve`) and report
its URL. This is the SpotGamma/MenthorQ-style view (net GEX by strike, IV skew, volume) served on
localhost — read-only and loopback-only. It is the gex **module's own** dashboard, distinct from the
suite dashboard (`/serve-dashboard`, default port 8787).

Do this:

1. **Confirm gex is installed first.** Check that the gex package checkout is present — i.e.
   `packages/gex/run.py` exists (from the repo root). If it does **not** exist, tell the user the gex
   module isn't installed (nothing to serve) and **STOP** — do not try to start anything or run any
   other command.

2. **Pick the port.** Use `$ARGUMENTS` if a port was given; otherwise use `serve.port` from the gex config
   (`~/.cherrypick/config/gex.json`, or the in-repo `packages/gex/config.json` until migrated) if set,
   else the default **5055**. (The dashboard also opens a WebSocket push on `serve.ws_port`, default
   `port + 1`, and the page falls back to polling if the socket is down.)

3. **Don't double-start.** Check whether something is already listening on that port
   (PowerShell: `Get-NetTCPConnection -LocalPort <port> -State Listen`). If a gex dashboard is already
   serving there, just report `http://127.0.0.1:<port>/` and stop — do not launch a second one.

4. **Start it in the background** (the server blocks with `serve_forever`, so it MUST run detached):
   run `python packages/gex/run.py dashboard --serve --port <port>` with `run_in_background: true`. Wait
   ~2s, then confirm it responds (HTTP 200 on `/`, e.g.
   `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/`).

5. **Report** the URL `http://127.0.0.1:<port>/`. Mention it's read-only + loopback-only; that live GEX
   needs the standalone streamer producing the shared cache (off-hours it shows the last cached state);
   and that to stop it later I can kill the process listening on that port.

Let it open the browser on this machine by default (don't pass `--no-browser`) unless I ask otherwise.
