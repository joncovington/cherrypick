# Going live safely

Everything in the suite is paper unless you deliberately turn live trading on. This page explains what
"live" means here, the locks that stand in front of it, how to arm for a day and how to stop. Read it
all before you arm anything.

Only MEIC has a live loop that ships on. Flies and bwb have live code too, but both are retired and
switched off, and their live loops refuse to arm while the module is off. Every other module is
paper-only or read-only. The rest of this page is about MEIC.

## What live means here

Live means **real orders, to your real brokerage account, with real money, that cannot be undone**. When
MEIC's live loop is armed it places four-leg iron condors on one index symbol for you, and it manages
their stops and settlement. Software fails: a stale quote, a broker outage or a bug can cost money in
ways no limit prevents. The caps, the breaker, the halt flag and the YES prompt reduce accidents. They
do not make trading safe.

[DISCLAIMER.md](../../DISCLAIMER.md) at the top of the repository is the full text. It is printed in
full every time you arm, and by arming you accept it. Read it once now, in particular the sections
"Live trading places real, irreversible orders" and "Paper and simulated results are not actual
trading". You alone are responsible for every order and every loss.

## The locks, in the order they stand

Live trading is behind several separate locks. All of them must be open at once.

1. **The module's master switch.** `enable_live_trading` in `~/.cherrypick/config/meic.json` is `false`
   until you set it to `true`.
2. **The module's live gate.** In the same file, the `live` block needs `live.symbol` (the one symbol it
   trades) and `live.gate0_confirmed`. The second is a sentence you write yourself, naming who attested
   and when, for example `"owner 2026-01-15"`. It records that you looked at the paper results first.
   It is empty until you fill it, and an empty one stops everything.
3. **Written kill criteria.** `live.kill` holds the arm's drawdown and losing-streak limits, written
   down and dated before it trades. With none, or with limits that have been crossed, new entries are
   refused. Crossing them never closes anything; it shrinks and then stops new entries.
4. **A designated account.** The suite trades only in the account you have designated, and refuses if
   none is. See [the account designation](#the-account-designation).
5. **The per-day arm.** Even with every lock above open, nothing opens a new condor until you arm for
   **today**. The arm lasts one trading day.
6. **The halt flag.** While it is up, no new live entry is placed, whatever else is open.

The settings editor and the console's Config page cannot change the guarded live fields (the master
switch, `live.symbol`, `live.gate0_confirmed`, `live.arm`, the caps and the breakers). You edit them by
hand in the file, on purpose: they should not move by accident.

Other limits sit inside the live block. `live.max_structures_per_day` caps new condors a day (1 if you
set nothing). `live.max_open_bp_dollars` caps the buying power the open condors may use.
`live.daily_loss_halt_dollars` stops new entries once the day's gross loss reaches that amount. The
shipped example sets it to 200; with it unset, the arming readout warns you. Live quantity is capped at
one contract per condor, and the loop refuses to arm if the config asks for more.

## What the per-day arm does

Arming writes an **arm record** for today, `~/.cherrypick/state/meic-live-arm.json`. The orchestrator's
supervisor runs the `meic-live` job once a minute, and the record is what lets that job open new
condors. Three things follow.

- **It lasts one day.** The loop disarms itself at `live.disarm_time` (17:00 ET by default). A record
  that is not today's is deleted at once. Tomorrow you arm again.
- **It needs the supervisor.** Without a running supervisor, nothing would tick, so arming is refused.
- **It opens no other lock.** Arming does not flip the master switch or write the live gate. It only says
  "today, yes".

Once armed, inside the entry window the loop looks for a condor that clears the live gates (IV rank,
GEX, distance from the money, VIX and ATR pauses). If one fits under the buying-power cap with
what is already open, it places a four-leg limit order on `live.symbol`. A fill is recorded only when the
broker confirms it.

### What runs when you are not armed

Not armed does not mean not running. On every trading day, from the open to the disarm cutoff, the
`meic-live` job still ticks. If the book holds a condor or a pending order, the unarmed tick
**manages it**: it confirms fills, polls and sends stops, force-closes where the rules say to, and settles
on the official closing print. What it will not do is open anything new; entries are refused as
`not_armed`. If the book holds nothing, the tick ends without calling the broker.

This is why disarming mid-day is safe. It stops new condors and leaves the ones you hold under their stops.

Turning `enable_live_trading` off is different and dangerous: it stops management too, and nothing at
the broker is touched. While positions are held the watchdog calls that CRITICAL. To stop new trades and
keep managing, use the halt flag or disarm. Never use the master switch.

## The account designation

The suite trades live only in the account you designate, never "the first account on the login". With
none designated, the live loop and the broker command refuse.

Run the wizard once (it asks for your tastytrade login, then the account, then optional webhooks):

```bash
python packages/orchestrator/run.py connect
```

To list, set or clear the designation yourself:

```bash
python packages/orchestrator/run.py account
python packages/orchestrator/run.py account --module meic --set 0000
python packages/orchestrator/run.py account --module meic --clear
```

`--set` takes the last four digits of the account or its index in the list. The suite shows accounts
masked, as `****0000`, everywhere. `account` changes configuration only; it places nothing.

## A second tastytrade login

The designation, and every live order, stay on the login you connected first. Another tastytrade
login (one holding a margin account, say) can be added so the suite reads its accounts too: positions,
balances, reconcile, manage's evaluation, the console's Live pages and the order alerts. Nothing
trades it.

```bash
python packages/orchestrator/run.py connect --login margin
python packages/orchestrator/run.py account --login margin
python packages/orchestrator/run.py supervise --restart
```

`connect --login` asks for that login's OAuth client secret and refresh token (hidden; stored in its own
keyring entry, `cherrypick-broker.margin`), checks the connection, then asks for a label and whether
manage should evaluate its accounts. Say yes for an account you trade by hand: its positions are then
expected, not reported as drift. It adds the login to `broker.logins` in `config.json`, backing the file
up first. Pick a generic name (`margin`, `ira2`), never your tastytrade username or account digits: it
appears in file names, job names and notifications. An account on that login is shown as
`margin ****0000`.

Restart the supervisor so the login's order-alert service (`account-alerts.margin`) starts. If the
login holds more than one account, add `"alerts_account": "<last 4>"` to its `broker.logins` entry to
say which one the service watches. `account --login` lists; it cannot designate. A login whose token
stops working shows as that login's REST read failing in the watchdog, and its accounts as unread; the
main login carries on. To remove it, delete its entry from `broker.logins` and run
`python -m cherrypick.core.auth remove --login margin`.

## Two ways to arm

Both write the same arm record and run the same checks. Neither is easier to get wrong than the other.
Both start by asking the loop whether it is ready.

### With Claude Code: `/live-meic-start`

From the repository root, in Claude Code, on a trading day:

```
/live-meic-start
```

The command reads the loop's status, then **tells you in plain words** what it is about to do before it
asks you anything. It reports:

- whether it is ready to arm, and each blocker if not (in which case it stops and asks nothing);
- the symbol, the arm it trades, the entry window, the buying-power cap, the condor count cap, whether
  per-side stops are on, the stop ratio and the daily-loss breaker;
- open positions, pending orders, today's entries and whether the breaker has tripped;
- whether the halt flag is up, and whether the supervisor is running.

It then prints the disclaimer and asks for a fresh, explicit **YES**. An earlier YES, in this
conversation or any other, covers nothing. Anything but YES cancels with nothing done. If the halt flag
is up, a YES also clears it, and the command says so. After a YES it arms, checks the result and tells
you the armed-for date, the entry window, the self-disarm time and where to watch the log
(`~/.cherrypick/logs/meic/meic_live.log`).

To disarm early: `/live-meic-start --stop`.

### Without Claude Code: the command line

Each live module's loop arms from a terminal. Activate the suite's virtual environment first (see
[QUICKSTART.md](../../QUICKSTART.md)), then look at the status:

```bash
python -m cherrypick.meic.live_loop --status
```

It prints JSON and makes no broker call. Read `arm_ready` first. If `ready` is `false`, the `blockers`
say exactly why, and arming will refuse until you fix each one. The `warnings` are things you may
proceed past but should understand. Then arm:

```bash
python -m cherrypick.meic.live_loop --arm --confirm YES
```

This prints `DISCLAIMER.md` in full. It arms nothing unless the confirmation is exactly `YES`, in
capitals. Without `--confirm YES` it prints the disclaimer, says it armed nothing, and exits. To disarm:

```bash
python -m cherrypick.meic.live_loop --disarm
```

Two things differ from the slash command. The command line does not clear the halt flag for you; if
it is up, new entries stay blocked until you clear it (see [the halt flag](#the-halt-flag)). And
`--install-task`, the flag the slash command uses after its own YES, prompts for nothing. Never type it
yourself to skip the disclaimer.

The flies and bwb loops have the same `--arm` and `--disarm` flags. While those modules are off, which
is how they ship, `--arm` refuses and writes nothing.

## The halt flag

The halt flag is the suite-wide stop for new live entries. It is a file, `~/.cherrypick/state/halt-live.flag`.
Its presence is the signal; its contents are ignored. Every live loop checks it each tick.

- **Raise it** with the **Halt live entries** button at the top of the console's Config page (one
  click), or by creating the file yourself.
- **It stops new entries only.** A condor already open keeps its stops and is force-closed and settled
  by the usual rules. Halting closes nothing.
- **Clear it** with "Resume live entries..." on the Config page: type `RESUME LIVE` exactly. Or delete
  the file. Clearing it does not arm anything; the locks above still decide whether the loop trades.
- The orchestrator's watchdog can raise it too, as a backstop after a failed self-disarm. If it is up
  when you did not raise it, find out why before clearing it.

When in doubt, halt first. Halting is free and reversible.

## How to check you are armed

- **Live page, `meic` tab.** The header chip reads `meic · ARMED · <symbol>` when today's record is valid
  and `meic · not armed · <symbol>` when it is not. It reads `HALTED` in place of those when the halt
  flag is up, and `stale arm` for a record that is not today's. The Live `today` tab shows the same
  state as a count, such as `1 armed`. See [live.md](live.md).
- **The loop's own status.** `python -m cherrypick.meic.live_loop --status` shows `armed_for` (today's
  date when armed), `halt_flag`, open positions, pending orders and `entries_today`.
- **The scheduler.** `python packages/orchestrator/run.py status` should list the `meic-live` job as
  enabled with a future `next_run`.

## How to stop

| You want to | Do this | What happens to what you hold |
| --- | --- | --- |
| Stop new condors for today | `/live-meic-start --stop`, or `python -m cherrypick.meic.live_loop --disarm` | Open condors are still managed until the disarm cutoff. A resting entry order at the broker is **not** cancelled; a fill on it is still confirmed and managed, or you can cancel it in your broker. |
| Stop new condors everywhere, at once | The halt flag | The same: open condors keep their stops. |
| Stop every module and service | `uninstall.cmd` or `./uninstall.sh` | The suite stops managing. Anything open at the broker stays open, so close it yourself first. |

A condor still held after the cutoff is settled by the hourly `settle-overdue-live` job at that day's
official close. You can also settle a session by hand:

```bash
python -m cherrypick.meic.live_loop --settle --date 2026-01-15 --price 5000.00
```

Use the official closing print for `--price`, not an estimate.

## Start small, and watch the first days

- **Start with the smallest size.** Live quantity is one contract, and `live.max_structures_per_day`
  defaults to 1. Keep `live.max_open_bp_dollars` and the daily-loss breaker low. Do not lift the count cap
  to `null` or raise the buying-power cap until you have watched many sessions.
- **Arm on a day you can watch.** Be at the screen for the first entries. Check the `meic` tab on Live and
  your broker's own platform against each other.
- **Compare live with the paper `live-shadow` arm.** MEIC runs a paper arm named `live-shadow` that
  follows the live settings exactly, so it is live's paper twin. Open `/meic` and look at `arms`. A live
  condor should look like what `live-shadow` did that day. If live takes trades `live-shadow` would not, or
  the reverse, find out why before you do anything else. Remember that paper is an upper bound: live fills
  are usually a little worse. See [reading-results.md](reading-results.md).
- **Check that fees reconcile.** Each morning the `meic-fee-reconcile` job writes the broker's actual
  fees onto settled live condors. To see what it would change, without writing anything:

  ```bash
  python -m cherrypick.meic.fee_reconcile
  ```

  Add `--apply` to write. If the recorded fees and your broker's statement disagree, stop and sort it
  out. Live net is only as honest as its fees.
- **Don't change settings mid-session.** Changing a gate, a stop or the entry window starts a new
  measurement. Make the change, then give it a fresh run before comparing with the old one.

## When something looks wrong

- Raise the halt flag first. Then open the Live `meic` tab and the broker's own platform.
- A condor open at the broker but unknown to the suite, or the reverse, shows up as a mismatch in the
  watchdog's `live-positions` check. The loop refuses new entries while it sees an order it cannot match.
  Resolve it in your broker, then run `--status`.
- `python packages/orchestrator/run.py doctor` lists what is not ready, in plain words.
