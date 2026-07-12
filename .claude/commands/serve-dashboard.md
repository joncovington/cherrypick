---
description: Start the cherrypick live dashboard server (dashboard --serve) and report its URL
argument-hint: [port]
---

Start the cherrypick **live** status dashboard (`python packages/orchestrator/run.py dashboard --serve`) and report its URL.
This is the live server view (System live-checks, the reconcile card, and any enabled embedded module
dashboards) — not the static `dashboard.html` file. It is read-only and loopback-only.

Do this:

1. **Pick the port.** Use `$ARGUMENTS` if a port was given; otherwise use `dashboard.serve.port` from
   `config.json` if set, else the default **8787**.

2. **Don't double-start.** Check whether something is already listening on that port
   (PowerShell: `Get-NetTCPConnection -LocalPort <port> -State Listen`). If a dashboard is already
   serving there, just report `http://127.0.0.1:<port>/` and stop — do not launch a second one.

3. **Start it in the background** (the server blocks with `serve_forever`, so it MUST run detached):
   run `python packages/orchestrator/run.py dashboard --serve --port <port>` with `run_in_background: true`. Wait ~2s, then
   confirm it responds (HTTP 200 on `/`, e.g. `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/`).

4. **Report** the URL `http://127.0.0.1:<port>/`. Mention it's read-only + loopback-only, and that to stop
   it later I can kill the process listening on that port.

Let it open the browser on this machine by default (don't pass `--no-browser`) unless I ask otherwise.
