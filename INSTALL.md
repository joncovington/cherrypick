# Install

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. **Read [DISCLAIMER.md](DISCLAIMER.md) before use.**

**Not a programmer?** Start with [QUICKSTART.md](QUICKSTART.md) instead — the same install, step by
step in plain language, including where to get your tastytrade keys.

This page is the reference: what you need, what the installers do, and the developer path. For what
the suite does once it is running, see the [README](README.md) and the [User Guide](docs/PROJECT.md).

## What you need

| | Required? | Notes |
|---|---|---|
| A [tastytrade](https://tastytrade.com) account | **Yes** | The market data every paper engine fills against. An OAuth client secret and a refresh token; a **read-only** grant is right for paper mode. |
| **Python 3.11+** | **Yes** | Windows: `winget install -e --id Python.Python.3.13`. macOS: `brew install python@3.13`. Linux: your package manager (on Debian/Ubuntu also `python3-venv`). |
| **Node.js 22+** | **Yes** | For the console, the suite's web UI. Windows: `winget install -e --id OpenJS.NodeJS.LTS`. macOS: `brew install node`. |
| **pnpm 11** | Installed for you | The installer runs `npm install -g pnpm@11` if pnpm is missing. |
| git | Optional | Downloading the ZIP from GitHub works just as well. |
| [Dolt](https://github.com/dolthub/dolt) | Optional | Needed only by the **earnings** module and the **technicals** report. It serves three public DoltHub datasets: `stocks` (about 3 GB), `earnings` (about 1.35 GB) and `options` (large — the biggest of the three). Without it, those two features are switched off and hidden. |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | Optional | Needed only by the AI advisor and the end-of-day and morning narratives, and for the repo's slash commands. Without it, those features are switched off and hidden. |
| A machine that stays awake in market hours | **Yes** | It runs on your computer, not a cloud service. Windows is the most proven platform (see [below](#macos-and-linux)); on Windows, see [Leaving a Windows PC unattended](#leaving-a-windows-pc-unattended). |

The installers check Python and Node before doing anything else, and print the command to install
whichever is missing.

## Windows

1. **Get the code.** Download the [latest release][latest] (under **Assets**, **Source code
   (zip)**, then unzip it somewhere easy to find, such as `Documents`), or clone it with git.
2. **Run the installer.** Either double-click **`install.cmd`** in the cherrypick folder, or open
   PowerShell, go to the folder first, and run it from there:

   ```powershell
   cd C:\path\to\cherrypick          # the folder that contains install.cmd
   .\install.cmd                      # or: powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   With git instead of the ZIP:

   ```powershell
   git clone https://github.com/joncovington/cherrypick.git
   cd cherrypick
   git checkout (git describe --tags --abbrev=0 origin/main)   # the latest release
   .\install.cmd
   ```
3. **Set the PC up to run unattended:** Windows Update's restart hours, sleep, power cuts, and how
   the suite comes back after a restart. See
   [Leaving a Windows PC unattended](#leaving-a-windows-pc-unattended). The installer checks the
   update hours and warns if Windows could restart during the suite's day.

## macOS and Linux

1. **Get the code**: the [latest release][latest]'s ZIP works here too (unzip it and `cd` into the
   folder), or with git:

   ```bash
   git clone https://github.com/joncovington/cherrypick.git
   cd cherrypick
   git checkout "$(git describe --tags --abbrev=0 origin/main)"   # the latest release
   ```

2. **Go to the folder, then run the installer** from inside it:

   ```bash
   cd ~/path/to/cherrypick              # the folder that contains install.sh
   ./install.sh                         # or: bash install.sh
   ```

On macOS and Linux the supervisor's anchor is a tagged entry in your user crontab. That backend is
newer than the Windows Task Scheduler one and less proven on a real host — check
`python packages/orchestrator/run.py status` after the first trading session, and report anything odd.

Cron gives a job almost no environment, so **run the installer from the shell you normally use**.
Each crontab entry the suite writes carries that shell's `PATH` (so `node`, `dolt` and `claude` are
found), `PYTHON_KEYRING_BACKEND` if you set one, the session D-Bus on Linux, and
`TZ=America/New_York`. That last one keeps a host on UTC or Pacific time from dating an evening
job with the wrong session. If you later install Node, Dolt or Claude somewhere new, run
`./install.sh` again to refresh the entries.

**Where the broker login is kept:**

| Host | Keyring | What to do |
|---|---|---|
| macOS | Keychain | Works while you are logged in. A Mac logged out or with a locked keychain refuses cron's read; stay logged in during market hours. |
| Linux laptop or desktop | Secret Service (GNOME Keyring, KWallet) over D-Bus | Works while you are logged in to the desktop, since your login unlocks it. |
| Headless Linux server | none | There is nothing to store the login in, and the installer's broker-login step fails with "No keyring backend available." Add a file backend and run it again: `.venv/bin/pip install keyrings.alt`, `export PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring`, then `./install.sh` from that same shell (its crontab entries keep the setting). This stores the login unencrypted under `~/.local/share/python_keyring/`, readable by your user, so keep the account to yourself. `keyrings.cryptfile` encrypts, but it asks for a password on every read and cannot run unattended. |

The `desktop` notification channel uses the notification centre on macOS and `notify-send` on Linux,
and only reaches you while you are logged in. For an alert that always arrives, set up a Discord or
Slack webhook (`run.py connect`, or `run.py secrets-set --channel discord`).

The optional browser collectors use Playwright, which no installer adds. Install it into `.venv`
yourself (`.venv/bin/pip install playwright`), then add the browsers:
- **QuikOptions** needs Google Chrome itself: install it the usual way on macOS, or run
  `.venv/bin/playwright install chrome` on x86-64 Linux. There is no Chrome for Linux on ARM.
- **The screener collector** uses Playwright's own Chromium:
  `.venv/bin/playwright install --with-deps chromium`. On Linux this installs system libraries and
  needs sudo.

Both run headless on schedule. Signing in the first time opens a visible browser, so a headless
server needs a display for that one step (a desktop session, VNC or `xvfb-run`).

Install from a release, not from `main`: `main` is where development happens and is often ahead of
the latest release. The git lines above check out the newest release tag, which leaves git in
"detached HEAD", as expected. How releases are cut: [docs/releasing.md](docs/releasing.md).

[latest]: https://github.com/joncovington/cherrypick/releases/latest

## What the installer does

Both installers follow the same steps, and both are safe to run again: they reuse what is already
there, never overwrite your configuration, and are how you add Dolt or Claude Code later.

1. **Disclaimer.** You type `YES` to confirm you have read and accept [DISCLAIMER.md](DISCLAIMER.md).
   Nothing is installed otherwise.
2. **Prerequisites.** Python 3.11+ and Node 22+ are checked; pnpm 11 is installed if missing.
3. **Python environment.** A virtual environment is created at `.venv` inside the checkout, and
   `packages/core` is installed first (every other package depends on it, and it is not on PyPI),
   then every other package **except `packages/desk`**. The desk is an EXPERIMENTAL prototype for
   educational purposes only, not installed or enabled by default — see
   [packages/desk/README.md](packages/desk/README.md).
4. **Console.** `pnpm install` and `pnpm build` in `packages/console`.
5. **Configuration.** `run.py init` writes `~/.cherrypick/config.json` from the annotated template,
   and keeps it if it already exists.
6. **Capabilities.** `run.py capabilities --detect --write` probes for Claude Code and Dolt and
   records what it finds in the config's `capabilities` block. A feature whose capability is missing
   stays off and is hidden in the console.
7. **Dolt (optional, skippable).** If you say yes, Dolt is installed where it can be (winget on
   Windows; on macOS and Linux it prints the command), and `post-no-preference/earnings`, `options`
   and `stocks` are cloned into `~/.cherrypick/data/earnings`. This is the slow part: several GB, and
   an hour or more on a slow connection. An interrupted clone resumes on the next run.
8. **Broker login.** `python -m cherrypick.core.auth setup` asks for your OAuth `client_secret` and
   `refresh_token` (hidden input) and stores them in the OS keyring — Windows Credential Manager,
   the macOS Keychain or the Linux Secret Service — never in a file.
9. **Settings history (optional, off unless you say yes).** `run.py config-backup --init --enable
   [--remote <url>]` makes `~/.cherrypick` a git repository that tracks only `config.json` and
   `config/*.json`, and the supervisor's `config-backup` job commits changes every 15 minutes and
   pushes them to the remote, if you gave one. Use a **private** remote. Needs git; skipped without it.
   Switch it on or off later on the console's Config page or with `run.py config-backup --enable` /
   `--disable`.
10. **Start.** `run.py install` registers the one anchor task and starts the supervisor, which then
   starts the data feed, every enabled module's paper loop and the console.
11. **Open** <http://127.0.0.1:5070>.

| Option (Windows / macOS and Linux) | Effect |
|---|---|
| `-AcceptDisclaimer` / `--accept-disclaimer` | You have read DISCLAIMER.md and accept it; no prompt. |
| `-Yes` / `--yes` | Accept every default without asking (Dolt: no; connect: yes). The disclaimer still needs `-AcceptDisclaimer`. |
| `-SkipDolt` / `--skip-dolt` | Do not offer the Dolt setup. Earnings and technicals stay off. |
| `-WithDesk` / `--with-desk` | Also install the EXPERIMENTAL manual desk. Installing it does not enable it. |
| `-ConfigHistory` / `--config-history` | Keep a git history of your settings without being asked (add `-ConfigRemote <url>` / `--config-remote <url>` to push it to a private repository you own). |
| `-NoStart` / `--no-start` | Install only. Start later with `.venv/bin/python packages/orchestrator/run.py install` (`.venv\Scripts\python` on Windows). |

**The supervisor runs the interpreter that ran `install`** — the installer's `.venv`. Run your own
commands through it too; see [Using the virtual environment](#using-the-virtual-environment).

## Using the virtual environment

The installer puts cherrypick and everything it needs into a **virtual environment**, the `.venv`
folder inside the checkout, rather than into your system Python. Everything the suite runs in the
background already uses it. When **you** run a cherrypick command by hand, activate it first, so that
`python` means the suite's Python — with the packages installed — and not some other Python on your
machine. (The supervisor runs on whichever interpreter ran `install`; running a command such as
`run.py install` from a different Python would switch the suite over to that one.)

Open a terminal, go to the checkout, and activate:

| Shell | Go to the checkout | Activate |
|---|---|---|
| Windows PowerShell | `cd C:\path\to\cherrypick` | `.venv\Scripts\Activate.ps1` |
| Windows Command Prompt | `cd C:\path\to\cherrypick` | `.venv\Scripts\activate.bat` |
| macOS / Linux | `cd ~/path/to/cherrypick` | `source .venv/bin/activate` |

Your prompt then starts with `(.venv)`. `deactivate` leaves it. If PowerShell refuses with "running
scripts is disabled on this system", allow local scripts for your user once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

(Or skip activation and call the interpreter directly: `.venv\Scripts\python` on Windows,
`.venv/bin/python` elsewhere, in place of `python` below.)

With the environment active, from the checkout:

```bash
python packages/orchestrator/run.py doctor        # green/red readiness — read-only, safe any time
python packages/orchestrator/run.py status        # what the supervisor is running
python packages/orchestrator/run.py capabilities  # which optional features this machine can carry
python packages/orchestrator/run.py connect       # account designation and Discord/Slack webhooks
python packages/orchestrator/run.py notify-test   # fire a test notification through your channels
python packages/orchestrator/run.py report        # paper P&L once data has accumulated
```

Modules are switched on and off on the console's **Config** page, or under `modules.<name>.enabled`
in `~/.cherrypick/config.json`. Calendars, PMCC and curve are EXPERIMENTAL and ship switched off.
Push notifications are off by default too: alerts go only to the suite's log until you add
`desktop`, `discord` or `slack` to the `notify` channel lists (the console's own on-screen toasts need
no setting).

## After installing: viewing the console

> **⚠️ The console is not hardened. Keep it on this computer.** It has no login and was not built
> to face a network: anyone who can reach it sees your positions and results and can use its
> Config page, including the live-trading halt switch. It listens only on `127.0.0.1` (this
> computer) by design. **Never expose it to your local network or the internet**: no port
> forwarding, reverse proxy, tunnel (ngrok and the like) or remote-access sharing of the page.
> The settings editor (`run.py settings`, port 8804) is under the same rule.

The console is the web page where you look at everything: open **<http://127.0.0.1:5070>** in your
browser and bookmark it. It listens on loopback only, so only this computer can open it. There is
nothing to start by hand: the supervisor keeps it running in the background, restarts it if it dies,
and brings it back after a reboot (through the one scheduled task or crontab entry `install`
registered).

If the page does not load, wait a minute and refresh, then check from the activated environment:

```bash
python packages/orchestrator/run.py status            # is the supervisor up, and the `console` job running?
python packages/orchestrator/run.py doctor            # what is wrong, in plain words
python packages/orchestrator/run.py restart console   # restart just the console
```

**Optional: a desktop window.** The console can also open in its own window (an Electron shell with a
tray icon). It is a window only — it never starts the server, so the supervisor's console must be
running. Double-click **`console-desktop.cmd`** in the installation folder on Windows, or run
**`./console-desktop.sh`** there on macOS and Linux. Both run `pnpm start` in
`packages/console/desktop`, which builds the shell first, so the first launch takes a minute. Keep the
terminal it opens; closing the console window ends it.

## Leaving a Windows PC unattended

The suite runs on your computer, so the computer has to be on, awake and running it all trading
day. On Windows that means four settings, plus a choice of how the suite comes back after a
restart.

**1. Windows Update restart hours.** Windows installs updates by itself and restarts outside its
*active hours*. By default that is whenever it judges the PC idle, which can fall right on the
morning data fetches or a live session. Windows Home cannot turn automatic updates off, and you
would not want it to: security fixes matter on a machine that holds your broker login. Move the
restarts out of the suite's day instead:

- Settings → Windows Update → Advanced options → **Active hours**: set it to **Manually** and cover
  the suite's day. Its jobs run from about **05:30 to 20:00 Eastern**. Windows allows 18 hours at
  most, so on Mountain time 03:00–21:00 is a good choice (05:00–23:00 Eastern). Restarts then
  happen overnight, when nothing but the backup runs, and the backup catches up.
- In the same place, turn **Get the latest updates as soon as they're available** off. That keeps
  the security fixes and drops the early feature updates.

`run.py install` and `run.py doctor` check this for you (`windows_update`). They read the active
hours, compare them with the suite's own schedule on this PC's clock, and warn with the exact
hours to set when a restart could land inside the suite's day.

**2. Sleep.** Settings → System → Power: set **Sleep when plugged in** to **Never**. A sleeping PC
runs nothing. On battery the suite warns you every 15 minutes (`power-watch`).

**3. Power cuts.** In the PC's BIOS/UEFI setup, set **Restore on AC Power Loss** (some boards call
it "AC Back" or "After Power Failure") to **Power On**. The PC then boots by itself when power
returns. Without it, a power cut leaves the machine off until someone presses the button.

**4. Coming back after a restart: pick one.**

- **Stay signed in (the default).** The suite's 2-minute check runs only while you are signed in,
  so turn on Windows **auto-logon** (Sysinternals *Autologon* sets it up safely) and lock the
  screen at every sign-in, so the signed-in desktop is not left open:
  ```
  schtasks /Create /TN LockOnLogon /SC ONLOGON /TR "rundll32.exe user32.dll,LockWorkStation" /RL LIMITED
  ```
  If your Windows account is a Microsoft account, auto-logon stops working after the password
  changes (the login screen shows a network error at boot). Run Autologon again with the new
  password.
- **Run as a Windows service (nobody signed in).** The PC boots to the login screen, and the suite
  runs anyway. See the next section.

## Optional: run as a Windows service (nobody logged on)

The suite's supervisor can run as a Windows service, so it starts at boot without anyone signing
in. This is opt-in and off until you turn it on.

1. Download `WinSW-x64.exe` (version 2.x) from the
   [WinSW releases page](https://github.com/winsw/winsw/releases).
2. In `~/.cherrypick/config.json`, set `service.enabled` to `true` and `service.winsw_exe` to the
   downloaded file's path.
3. Run `python packages/orchestrator/run.py service prepare`. It writes the service definition
   (with the PATH Windows gives you at sign-in, so `node`, `dolt` and `claude` are found) and
   prints five commands.
4. Run them in that order, in an **administrator** PowerShell:
   1. **Install.** It asks for your Windows account and password, which Windows stores; they
      never go in a file. Enter the account as **`.\yourname`** (a bare name is refused). If your
      Windows account is a Microsoft account, the password is your **Microsoft account
      password**, not your PIN.
   2. **Grant "Log on as a service".** The install does not always grant this right. Windows Home
      has no Local Security Policy editor, so the suite's script
      (`scripts\windows\grant-logon-as-service.ps1`) grants it with Windows' own `secedit`. It is
      safe to run twice.
   3. **Stop the running supervisor**, so the service can take over (no administrator needed).
   4. **Start the service.**
   5. **Let the 2-minute check run while you are signed out.** It asks for your password again.
      The service restarts a supervisor that exits, but the check is what restarts one that hangs.
      In Task Scheduler this shows as "Run whether user is logged on or not".
5. Restart the PC once, leave it at the login screen for five minutes, then sign in and run
   `run.py doctor`. The `service` line should say running, and `supervisor.log` should show the
   supervisor starting before you signed in. Then turn auto-logon off and delete `LockOnLogon`.

**If the service will not start**, Windows' System event log (Event Viewer → Windows Logs →
System, source *Service Control Manager*) says why:

- **7038, "user name or password is incorrect":** re-enter the password in the Services app
  (`services.msc` → *cherrypick supervisor* → Properties → **Log On**). On a Microsoft account,
  Windows must have seen that password at a sign-in on this PC: if you always use a PIN, sign out
  and sign in once with the password first.
- **7041, "not been granted the requested logon type":** run step 4.2 again.

Until it starts, the 2-minute check gives the service 5 minutes to recover, then runs the
supervisor the usual way and sends a CRITICAL saying so. The suite keeps running either way.

**Living with it:**

- **Nothing appears on screen.** Desktop notifications are skipped, so use Discord or Slack. A
  collector whose website sign-in has expired is caught by its morning browser check, which warns
  you; sign in from your desktop with its `login` command.
- **After updating the code,** run `run.py supervise --restart`. The service brings the
  supervisor back with the new code in about 30 seconds. A plain `--stop` would leave the service
  stopped until an administrator starts it.
- **After changing your Windows password** (for a Microsoft account, changing it online counts),
  re-enter it in two places: the Services app's Log On tab, and Task Scheduler (*cherrypick-supervisor*
  → Properties → OK, then the password). Until then the suite cannot start at boot.
- To undo it all, run `run.py service uninstall` and follow what it prints.

## Stopping and uninstalling

Double-click **`uninstall.cmd`** on Windows, or run **`./uninstall.sh`** on macOS and Linux. It is a
full stop:

1. `run.py uninstall` — removes the anchor task (or crontab entry) and stops the supervisor and its
   services;
2. `run.py stop --all` — stops the streamer, the console and anything else still running;
3. stops the Dolt server on port 3306, but only if the process listening there really is `dolt`.

Your data, configuration and broker login are **kept** (`~/.cherrypick` and the OS keyring), so
running the installer again picks up where you left off. To remove everything, delete
`~/.cherrypick` and the checkout by hand afterwards, and remove the `cherrypick-broker` entry from
your keyring.

## Updating to a new release

Stop the suite (`uninstall.cmd` / `./uninstall.sh`; history and settings are kept), get the new
release, and run the installer again. With the ZIP, unzip the new release over the old folder. With
git, from the checkout:

```bash
git fetch --tags origin
git checkout "$(git describe --tags --abbrev=0 origin/main)"
```

In PowerShell the second line is `git checkout (git describe --tags --abbrev=0 origin/main)`.

Read that release's notes on the [Releases page](https://github.com/joncovington/cherrypick/releases)
first: they say what changed and anything you need to do.

## For developers

`scripts/dev-install.ps1` and `scripts/dev-install.sh` are the developer path: editable installs
**with the `[dev]` extras** (pytest, ruff) of every package including desk, into whichever Python
you pass, followed by a console build if pnpm is present. They do not create a venv, write config,
detect capabilities or start anything.

```bash
./scripts/dev-install.sh .venv/bin/python                                # macOS / Linux / Git Bash
powershell -File scripts\dev-install.ps1 -Python .venv\Scripts\python.exe   # Windows
```

By hand, `packages/core` **must** go first — every other package imports it and there is no
`sys.path` fallback:

```bash
pip install -e "packages/core[dev]"
pip install -e "packages/orchestrator[dev]"   # and so on, per package
cd packages/console && pnpm install && pnpm build
```

Then `python packages/orchestrator/run.py init`, `connect`, `doctor` and `install`, as above. CI's
checks can be run locally with `scripts/ci-local.sh`.

Live trading is **off** by default everywhere, and nothing on this page enables it. Before you
consider changing that, read
[the warning in the README](README.md#before-you-go-anywhere-near-live-trading).
