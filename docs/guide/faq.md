# FAQ

Short answers to the questions people ask first. Where a longer guide exists, the answer points to it.

## Getting started

### What is this, in one sentence?

A set of tools that runs options strategies on paper using live market data, measures honestly
whether they make money after costs, and helps you manage the trades in your own account.

### Will it trade my money without asking me?

No. Everything is paper unless you turn live trading on for a module, and even then it only trades
on a day you arm it by typing YES. Each arm switches itself off at 5 pm Eastern by default (the
module's `disarm_time` setting). Only MEIC, flies and bwb carry live code.

### What do I need?

A computer that stays on during market hours, and a tastytrade account (it supplies the live
prices). Python 3.11 or newer and Node.js 22 or newer are needed too, and the installer checks for
them. Claude Code is optional, and so are Dolt (free earnings history) and a ThetaData subscription
(backtests).

### Does it cost anything?

The software is free (MIT licence). You pay only for things you choose to add, such as a data
subscription. Broker fees apply only if you trade live.

### Windows, Mac or Linux?

All three work. Windows is the best proven, because the scheduler and self-healing are most tested
there. The macOS and Linux scheduler is newer and less proven.

### Do I need to keep the console open?

No. A background service runs everything on schedule. The console only shows you what happened, so
open it whenever you like.

### The console won't open. Where is it?

Go to http://127.0.0.1:5070 in your browser. If nothing loads, run
`python packages/orchestrator/run.py doctor`, which checks what isn't ready (Python, config, broker
session, data feed, databases) and says which part is failing.

## Paper, live and safety

### What does "paper" mean here?

The suite pretends to place trades at real market prices and records what would have happened, fees
included. No order reaches your broker.

### Why does my paper result look better than live?

Paper fills are an estimate, and real fills are often a little worse. Paper is the best case, which
is why the suite shows fees and slippage in their own columns and doesn't hide them inside the
result.

### How do I stop everything right now?

Raise the halt flag with the **Halt live entries** button on the console's Config page (see *Going
live safely*). It stops any new live entry at once. Anything already open is still managed, stopped
and closed, so nothing is left unwatched. It stops live risk only; paper keeps running. To stop the
whole suite, run `uninstall.cmd` or `./uninstall.sh`, which keeps your history and settings.

### What happens if my computer crashes mid-trade?

Live positions still exist at your broker. When the suite comes back, it settles overdue live
sessions through the module's own command. The *Troubleshooting* guide walks through it.

### Why is a strategy marked "retired"?

It was tested and no longer earns its place. It ships switched off and can be switched back on, but
read why it was retired first.

### Can I trade earnings plays live?

Not anymore. From v0.12.0 the earnings module is paper-only: it has no live gate, and a leftover
live setting in its config is ignored.

## Day to day

### Why did nothing trade today?

Usually because a safety rule said no: volatility too low, an event day, a price limit not met.
Every skipped entry is written down with its reason on the module's decisions tab.

### When does everything run?

Data arrives early in the morning, the screens run before the open, entries happen during the day,
and results settle after the close, with a provisional review at 4:30 pm Eastern. *Your first week*
has the full timetable.

### What is an "arm"?

One version of a strategy with its own settings, for example the same condor with a wider stop. Arms
run side by side so you can compare them fairly. (The same word is used for something else when you
"arm" live trading for a day; the context makes it clear.)

### What does "provisional" mean on a result?

There aren't enough trades or sessions yet to trust it, so treat it as a hint, not a verdict. (On
the review it has a second meaning: the day's figures at the close, before they are final the next
morning.)

### What does "P&L" mean here?

Always the net result: after fees, settlement and slippage. Those three also get their own columns,
so you can see where the money went.

### Why does GEX lead with volume rather than open interest?

tastytrade does not offer level 2 data, so open interest does not stream. The suite only has the
broker's once-a-day count, as of the open, and it doesn't move during the session. GEX by volume (the
session's traded volume) is the reading that changes intraday, so the GEX pages show it by default.
The open-interest figures are still there, as positioning at the open. See
[Charts and GEX](charts-and-gex.md).

## Income trades (skate, wheel, PMCC)

### Does the suite place my wheel or PMCC trades?

No. You place them yourself. The suite reads your account (it can't change it) and tells you what
its rules suggest.

### My put was assigned. Where did it go?

Onto the Wheel page, in the same cycle as the put. You'll see the shares, what they really cost you
after all the premium, and the covered call the rules suggest next.

### What's the difference between the two costs it shows for my shares?

The tax basis is the price you got the shares at. The wheel basis is what they've really cost once
every premium, fee and roll in the cycle is counted.

### Why does my trade say "unreconciled"?

The suite couldn't rebuild the trade from your history: for example, the trade started before the
history the broker gave it, or an assignment couldn't be matched. Its figures are shown as far as
the rows go, but the rules don't judge it and you shouldn't trust it until that's fixed.

### What is the skate yield?

Premium divided by the capital at risk, scaled to a year. It lets you compare trades with each
other. It isn't a forecast of what you'll earn.

### Why are there two PMCC things?

The Live page's pmcc tab reads PMCCs in your own account. The old pmcc paper module is retired and
ships switched off. They're different things that share a name.

### Can I add a report of my own to the console?

Yes. Any script you write can drop one file in `~/.cherrypick/data/external/`, and it shows up under
Reports, then external, as its own tab. You can style it like the console's own tables and ask for it
to be posted to Discord. [External reports](external-reports.md) walks through it, and
[Discord](discord.md) covers the channels.

### Can Claude Code make a report for me?

Yes, if you use Claude Code in this repository. Describe the report you want and it picks up the
repository's `external-report` skill: it writes the script, checks the file it produces, and tells
you what it checked. You can also do all of it by hand.

## Data and privacy

### Where does my data live?

In a `.cherrypick` folder in your home folder, on your own computer. Back that folder up and you've
backed up everything. The suite also writes a nightly backup zip of its own data, on by default, to
`~/.cherrypick/backups`; point it at another drive to survive a lost disk.

### What leaves my computer?

Your trading data stays on your machine, but the suite does talk to the internet. By default:

- Requests to your broker (tastytrade) for prices and your positions.
- A check of GitHub for the latest release, at most every six hours, so the console's status bar can
  show a "new version available" chip. It sends none of your data. Set `"update_check": false` in
  `~/.cherrypick/config/console.json` to turn it off.
- Public data downloads, such as exchange and Treasury files fetched by scripts.

Only if you set them up:

- Dolt pulls of the public earnings and stock datasets from DoltHub.
- ThetaData requests for backtests.
- Discord, Slack or Telegram messages. Everything sent is logged by webhook name in
  `data/outbound/`, and `python packages/orchestrator/run.py sent` reads it back.
- If you use Claude Code with the advisor or the written narratives switched on, a summary of the
  day's numbers is sent to Claude to write about.

### Where are my passwords kept?

In your operating system's keychain (Windows Credential Manager, macOS Keychain or the Linux Secret
Service), never in a file, an environment variable or a log.

### Why does it show my account as `****1234`?

On purpose. The suite shows the account masked, with only the last four digits.

## Problems

### Things stopped running overnight.

Check whether the computer slept. A sleeping laptop misses its jobs. *Always-on machine* covers how
to stop that.

### I changed a setting and nothing happened.

Some jobs read their settings only when they start. Restart that job, or the suite, after editing.

### Discord isn't getting messages.

Run `python packages/orchestrator/run.py notify-test`. If nothing arrives, store the webhook again
with `python packages/orchestrator/run.py secrets-set --channel discord`.

### How do I upgrade?

The console's status bar chip tells you when a new release is out. Stop the suite (`uninstall.cmd`
or `./uninstall.sh`, which keeps your history and settings), get the new release, run the installer
again, and read that release's notes on the Releases page first: they say what changed and anything
you need to do.
