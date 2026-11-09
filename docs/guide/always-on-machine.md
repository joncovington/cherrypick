# Running the suite on an always-on machine

The suite runs on your computer, not in the cloud, so the computer has to be on, awake and online
while it works. A trading day with a hole in it is a hole in every record. This page covers what to
set so that a PC, and above all a laptop, keeps running unattended, and how the suite comes back after
a restart. The full install steps are in [INSTALL.md](../../INSTALL.md#leaving-a-windows-pc-unattended).

Most of this page is about Windows, which is the most proven platform. The last section covers macOS
and Linux.

## What you see when it works

- `run.py doctor` is green, or shows only warnings you understand. See [troubleshooting.md](troubleshooting.md).
- The status bar's `watchdog` chip reads `OK` with a small number of minutes.
- After a reboot the suite is running again before you sign in (service mode), or as soon as you do
  (the default).

## Keep Windows from interrupting the suite

Four things stop a PC from running a trading day. Fix each once.

### Sleep and hibernation

A sleeping computer runs nothing. The supervisor cannot fire a job while the machine is suspended, so
every job due in that time is late, or missed outright if its catch-up window closes first. Waking the
machine brings the supervisor back, but it cannot make up a quote record it never took.

Set **Settings → System → Power → Sleep** to **Never** when plugged in. On a laptop, also set what
closing the lid does to **Do nothing**, because the default sleeps the machine the moment the lid shuts.

`packages/orchestrator/tools/setup-walkaway-durability.ps1` does this for you, run once from an
administrator PowerShell:

```
powershell -ExecutionPolicy Bypass -File packages\orchestrator\tools\setup-walkaway-durability.ps1
```

It turns off sleep and hibernation, sets lid-close to do nothing, and pins Windows Update's active
hours to a quiet window. It never touches your password, and its changes can be undone. Read the top of
the script before you run it.

A laptop on battery is the other risk: when the battery runs out the machine stops. Keep it plugged in.
For that, the suite watches the power for you (see **Battery warnings** below).

### Windows Update restarts

Windows installs updates by itself and restarts outside its **active hours**. By default that is
whenever it judges the PC idle, which can land on the morning data fetches or a live session. Home
editions cannot turn automatic updates off, and you would not want to: security fixes matter on a
machine that holds your broker login. Move the restarts out of the suite's day instead.

`doctor` checks this for you, in a line called `windows_update`. It reads your active hours, compares
them with the hours the suite needs on your clock, and when a restart could land inside the day it
warns and says the exact hours to set. A typical fix:

1. Open **Settings → Windows Update → Advanced options → Active hours**.
2. Choose **Manually**, then set the start and end `doctor` gave you. The suite's day runs from about
   05:30 to 20:00 Eastern. Windows allows at most 18 hours, so the overnight backup is allowed to fall
   outside. On Mountain time, 03:00 to 21:00 works. The warning is computed on your own clock.
3. Turn off **Get the latest updates as soon as they're available**, in the same place. That keeps
   security fixes and drops early feature updates.

The warning also complains if the setting is **Automatically**, because Windows can move the hours.
`doctor` only reads the setting. It never changes it.

### Power cuts

A power cut turns the machine off, and it stays off until someone presses the button. In the PC's
BIOS or UEFI setup, set **Restore on AC Power Loss** (some boards call it "AC Back" or "After Power
Failure") to **Power On**. The PC then starts when the power returns.

### Battery warnings

The suite includes a job, `power-watch`, that tells you when the machine is running on battery. It
runs every minute around the session and every five minutes otherwise, every day of the week. It reads
only the computer's own power status, with no network.

- When the machine switches to battery, you get a **WARNING** on every channel you have set up (the log, a
  desktop toast, Discord, Slack), and again every 15 minutes while it stays there.
- When the charge falls to 20% or 30 minutes of estimated time, it becomes **CRITICAL**. If a live
  loop is armed that day, the message says so.
- When the mains returns, you get one message saying how long the machine was on battery.
- A reading it cannot take is never read as "plugged in" or "unplugged".

A desktop machine with no battery never alerts. To change the repeat time or the limits, or to turn it
off, use `power_watch` in `~/.cherrypick/config.json`: `repeat_minutes`, `critical_percent`,
`critical_minutes` and `enabled`. Set up Discord or Slack ([discord.md](discord.md)), because a toast
on a laptop you are not looking at is no help.

## Power throttling

Windows slows down background programs that have no visible window to save power. It calls this
**efficiency mode** (EcoQoS). On some processors, mostly the ones with a mix of fast and slow cores, a
throttled program can run several times slower than the same code by hand. The suite's jobs are
exactly this kind of program, and the slowdown would show up as late ticks and a slow console.

The suite opts out for you. The supervisor does it for itself and for every job it starts, and the
streamer, the Dolt server and the console are covered as well. There is nothing to set. To check:

- `supervisor.log` has a line when it starts, such as `supervisor started (pid 4242; power throttling off)`.
- In `run.py status`, each job has a `power_throttling` field that reads `off`.

If either reads `left to Windows`, the opt-out was refused for that process. Restart the supervisor
(see [troubleshooting.md](troubleshooting.md#restarting-the-supervisor)). A program you start by
hand in a terminal is not covered, and does not need to be. The opt-out is for Windows only, and it
is not inherited, so a process takes it the next time it starts.

## Coming back after a restart

When Windows restarts, for an update or any other reason, the suite has to start again by itself. There
are two ways, and you pick one.

### Stay signed in: the default

The installer registers one scheduled task, `cherrypick-supervisor`, which checks every two minutes
that the supervisor is alive and starts it if not. By default it runs only while you are signed in. So
that the PC signs in by itself after a restart, turn on Windows **auto-logon** (Microsoft's Sysinternals
*Autologon* sets it up safely) and lock the screen at every sign-in, so nobody can sit at an open
desktop:

```
schtasks /Create /TN LockOnLogon /SC ONLOGON /TR "rundll32.exe user32.dll,LockWorkStation" /RL LIMITED
```

If your Windows account is a Microsoft account, auto-logon stops working when you change the password
(the login screen shows a network error at boot). Run Autologon again with the new password. If you
use a Windows Hello PIN, a restart still asks for the PIN in front of auto-logon. Remove the PIN, or use
the service below.

### Run the supervisor as a Windows service

The alternative is to start the supervisor as a Windows service, so it comes up at boot with nobody
signed in. The PC can sit at the login screen, and the suite runs anyway. It is off until you choose it,
and the steps are in [INSTALL.md](../../INSTALL.md#optional-run-as-a-windows-service-nobody-logged-on).
In outline:

1. Download WinSW (`WinSW-x64.exe`, version 2.x) from its releases page.
2. Set `service.enabled` to `true` and `service.winsw_exe` to the file's path, in `~/.cherrypick/config.json`.
3. Run `python packages/orchestrator/run.py service prepare`. It writes the service definition and
   prints five commands.
4. Run those five, in order, in an **administrator** PowerShell. They install the service under your
   account (enter it as `.\yourname`; on a Microsoft account the password is the account password,
   not the PIN), grant it "Log on as a service", stop the running supervisor, start the service, and
   let the two-minute check run while you are signed out.
5. Restart the PC once, leave it at the login screen for five minutes, sign in, and run `doctor`. The
   `service` line should say the service is running. Then turn auto-logon off and delete `LockOnLogon`.

Check it any time with `python packages/orchestrator/run.py service status`. The service runs as you,
so the saved broker login stays readable. Its password is held by Windows and never goes in a file.

Living with it:

- **Nothing appears on screen.** Desktop toasts are skipped because the service has no desktop, so use
  Discord or Slack for alerts.
- **After you update the suite,** run `python packages/orchestrator/run.py supervise --restart`. A plain
  `--stop` leaves the service stopped until an administrator starts it.
- **The quote feed and Dolt run in the service's own session,** so Task Manager cannot stop them, even
  as you. Use `run.py restart streamer` and `run.py restart dolt-server`: the supervisor does it for you.
- **After you change your Windows password,** enter it again in two places: the Services app's Log On
  tab, and Task Scheduler (*cherrypick-supervisor* → Properties → OK, then the password). Until you do,
  the suite cannot start at boot.
- **If the service will not start,** Event Viewer → Windows Logs → System, source *Service Control
  Manager*, says why. Event 7038 means the password is wrong. Event 7041 means the "Log on as a service"
  right is missing.
- **To undo it,** run `python packages/orchestrator/run.py service uninstall` and follow what it prints.

Either way, the suite keeps running: until the service starts, the two-minute check waits five minutes
and then runs the supervisor itself, with a CRITICAL alert saying so.

## macOS and Linux

On macOS and Linux the same two-minute check is a tagged entry in your user crontab, written by the
installer. The supervisor and its jobs are the same. That backend is newer and
less proven than the Windows one, so check `python packages/orchestrator/run.py status` after your first
trading session and report anything odd.

- **Run the installer from the shell you normally use.** Cron gives a job almost no environment, so each
  entry carries that shell's `PATH` and the time zone `America/New_York`. If you later install Node, Dolt
  or Claude somewhere new, run `./install.sh` again to refresh the entries.
- **There is no WinSW, no Windows Update setting and no EcoQoS** to think about. `power-watch` works
  (macOS reads `pmset`, Linux reads the system power supply, and a Linux machine with no battery never
  alerts).
- **The login must be unlocked.** On macOS the Keychain refuses cron's read when you are logged out, so
  stay logged in during market hours. On a Linux desktop, the keyring unlocks when you log in. A
  headless Linux server has no keyring: [INSTALL.md](../../INSTALL.md#macos-and-linux) says what to add.
- **Stop it sleeping.** On a Mac, turn on **System Settings → Battery (or Energy) → Prevent automatic
  sleeping**. On Linux, switch off suspend in your power settings.
- The `desktop` alert channel only reaches you while you are logged in. Use Discord or Slack for
  alerts that must arrive.

## What to do

- Set sleep, the active hours and (for a PC) Restore on AC Power Loss once, in your first week.
- Pick auto-logon or the service, and check it with a restart on a weekend, not on a Monday.
- Set up a Discord or Slack webhook so that a battery or critical alert reaches you.
- Run `doctor` after every Windows update that restarted the machine.
- After an outage, see [troubleshooting.md](troubleshooting.md#after-an-outage).
