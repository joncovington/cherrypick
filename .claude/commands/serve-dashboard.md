---
description: Start/stop cherrypick dashboards (suite/gex/meic/flies/all); --<module> targets, --stop [module|all]
argument-hint: [port] | all | --gex|--meic|--flies [port] | --meic --paper | --stop [module|all] [port]
---

Start — or, with `--stop`, stop — a cherrypick live dashboard server. The default target is the **suite**
dashboard; `--gex` / `--meic` / `--flies` target a module's own dashboard, and `all` covers every
installed dashboard at once. Stops take an optional target: `--stop` (suite), `--stop <module>`, or
`--stop all`. All views are read-only and loopback-only (bound to `127.0.0.1`).

## Module registry

Every target below is one row. **To add a module later, add a row here** — the generic Start/Stop/all
procedures read this table, so no other part of this command needs to change.

| Target | Module | Installed if (from repo root) | Start command (blocks — run detached) | Default port | Browser |
|---|---|---|---|---|---|
| _(none)_ / `suite` | orchestrator suite | always (the repo itself) | `python packages/orchestrator/run.py dashboard --serve --port <port>` | `dashboard.serve.port` in `~/.cherrypick/config.json` (or in-repo `config.json`), else **8787** | opens by default; **suppress with `--no-browser`** |
| `--gex` | gex | `packages/gex/run.py` exists | `python packages/gex/run.py dashboard --serve --port <port>` | `serve.port` in `~/.cherrypick/config/gex.json` (or `packages/gex/config.json`), else **5055** (WebSocket push on `serve.ws_port`, default `port + 1`, same process) | opens by default; **suppress with `--no-browser`** |
| `--meic` | meic | `packages/meic/src/dashboard.py` exists | `python dashboard.py [--mode paper] --port <port>` — **must run with working dir `packages/meic/src`** (it does a bare `import paths`) | **5050** live / **5051** paper (`--paper` → `--mode paper`, paper_trades.db, "Paper Mode — Simulated") | opens by default; **suppress with `--no-browser`** |
| `--flies` | flies | `packages/flies/run.py` exists | `python packages/flies/run.py dashboard --port <port>` | `FLIES_DASHBOARD_PORT` env, else **8803** | opens by default; **suppress with `--no-browser`** |

All four modules share one browser convention: a dashboard opens a tab on start unless you pass
**`--no-browser`**.

## Route on `$ARGUMENTS`

Check `--stop` before the start flags:

- If it contains **`--stop`**, pick the stop target by the word that follows it:
  - `--stop gex` / `--stop meic` / `--stop flies` → **Stop a single dashboard** for that module.
  - `--stop all` → **Stop all dashboards**.
  - `--stop` with no target (or `--stop suite`) → **Stop a single dashboard** for the suite.
- Else if it contains **`all`** → **Start all dashboards**.
- Else if it contains **`--gex`** / **`--meic`** / **`--flies`** → **Start a single dashboard** for that module.
- Otherwise → **Start a single dashboard** for the suite. (Any bare number in `$ARGUMENTS` is the port.)

---

## Start a single dashboard

Using the chosen target's registry row:

1. **Confirm the module is installed** (the row's "Installed if" check). If it isn't, tell the user that
   module isn't installed (nothing to serve) and **STOP** — don't run anything else. (The suite is always
   installed.)

2. **Pick the port.** Use the bare number in `$ARGUMENTS` if given; otherwise the row's default. For
   `--meic`, `--paper` selects the paper row (port 5051) — otherwise live (5050).

3. **Don't double-start.** Check whether something is already listening on that port
   (`Get-NetTCPConnection -LocalPort <port> -State Listen`). If a dashboard is already serving there, just
   report `http://127.0.0.1:<port>/` and stop — do not launch a second one.

4. **Start it in the background** (the server blocks with `serve_forever`, so it MUST run detached): run
   the row's start command with `run_in_background: true` (for `--meic`, set the working directory to
   `packages/meic/src`). It opens the browser by default; don't pass `--no-browser` unless I asked not to
   open a tab. Wait ~2s, then confirm it responds (HTTP 200 on `/`, e.g.
   `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:<port>/`).

5. **Report** the URL `http://127.0.0.1:<port>/`. Mention it's read-only + loopback-only; for `--meic`
   `--paper` note "Paper Mode — simulated data only"; for `--gex`/`--flies` note live data needs the
   shared stream cache being produced (off-hours it shows the last cached state); and that to stop it later
   I can run `/serve-dashboard --stop <target>` (`--stop` alone for the suite).

---

## Start all dashboards

Start every **installed** dashboard in one shot, then open a **single** browser tab to the suite dashboard
once they're all up. Start each server **tab-less** (so none pop their own tab); the one intentional tab is
opened at the end.

1. **For each registry row, if the module is installed, run the Start-a-single-dashboard procedure** with
   its default port, **tab-less** — pass `--no-browser` to each. Use meic's **paper** row (`--mode paper`,
   port 5051) — `all` is a paper-collection view, so default meic to paper, not the live account. Skip any
   module that isn't installed and say so. Keep each start's don't-double-start check and ~2s HTTP-200
   confirmation. Ignore any bare port number in `$ARGUMENTS` for `all` (the targets have different ports —
   one port can't apply to all).

2. **Wait until every started dashboard confirms HTTP 200** before opening any browser, so the tab doesn't
   load before the suite server is ready.

3. **Open one browser tab to the suite dashboard** — `http://127.0.0.1:<suite-port>/` (the port resolved
   for the suite in step 1, default 8787), e.g. `start http://127.0.0.1:<suite-port>/`. Only the suite tab
   opens; the module dashboards stay tab-less (their URLs are in the report). If the suite failed to come
   up, don't open a tab.

4. **Report** a short list of every dashboard that's now up with its URL (and note any skipped because the
   module isn't installed, or already running). Mention they're read-only + loopback-only, that the suite
   tab was opened, and that `/serve-dashboard --stop all` stops them all.

---

## Stop a single dashboard

Using the chosen target's registry row. This only stops that dashboard's server process — it touches
nothing else in the suite (use `/uninstall` to fully stop everything).

1. **Confirm the module is installed** (the row's "Installed if" check). If it isn't, tell the user that
   module isn't installed (nothing to stop) and **STOP**. (The suite is always installed.)

2. **Pick the port(s).** Use the bare number in `$ARGUMENTS` if given; otherwise the row's default. For
   `--stop meic` with no port, target **both** meic ports — **5050** (live) and **5051** (paper) — and stop
   whichever are running.

3. **For each target port, find the listener** (`Get-NetTCPConnection -LocalPort <port> -State Listen`).
   If nothing is listening on a port, note it's not running and move on. If nothing is running on any
   target port, tell the user that dashboard isn't running and **STOP**.

4. **Confirm it's the right process before killing.** Look up the owning process
   (`Get-Process -Id <OwningProcess>`) and confirm it's this module's Python dashboard server (the process
   running the row's start command — e.g. `run.py dashboard --serve` for suite/gex, `dashboard.py` for
   meic, `run.py dashboard` for flies), not some unrelated service on that port. If it's clearly not the
   dashboard, report what's on the port and leave it alone.

5. **Stop it.** Kill the owning process for each confirmed port, e.g.
   `Get-NetTCPConnection -LocalPort <port> -State Listen | Select-Object -Expand OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }`.

6. **Confirm it's down.** Re-check each targeted port is no longer listening and report which dashboard(s)
   were stopped.

---

## Stop all dashboards

Stop every cherrypick dashboard that's running — every installed module — in one shot. This only stops the
dashboard server processes; it touches nothing else in the suite (use `/uninstall` to fully stop everything).

1. **For each registry row, if the module is installed, run the Stop-a-single-dashboard procedure** at its
   default port(s) (meic: both 5050 and 5051). Ignore any bare port number in `$ARGUMENTS` for `all`.

2. Keep the "confirm it's the right dashboard process before killing" guard, and treat "nothing listening"
   as simply not-running (note it and move on — don't abort the whole run).

3. **Report** a short summary of what was stopped and what wasn't running (or wasn't installed).
