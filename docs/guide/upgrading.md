# Upgrading

How to move an installed cherrypick to a newer release, and how to move back if the new one does not
suit you. The console's version chip (right end of the status bar) turns amber when a newer release is
out; this page is what you do about it.

## The short version

From the folder you installed, on a quiet evening after the close:

- **Windows:** double-click `upgrade.cmd`.
- **macOS and Linux:** run `./upgrade.sh`.
- **From Claude Code:** `/cherrypick-upgrade` reads the new release's notes to you in plain words, checks your
  settings against the keys the release changed, and then runs the same script.

It shows what the new release asks of you, waits for a yes, and does the rest. Everything it does is
in the script (`run.py upgrade`); the slash command only explains and calls it.

## What it does

1. **Checks first.** It shows the installed and the target version (`--to v0.13.0` names one; the
   default is the latest release), then lists every reason it will not start. Nothing has changed yet.
2. **Shows the notes.** The release's *Upgrading* notes, anything you have to do by hand, the config
   keys it changed and the data migrations it will run. If you are skipping releases, you get the notes
   of each one you skip. You type `yes` (`--yes` skips the question for scripts).
3. **Backs up.** It runs `backup` and verifies it, then keeps a copy named
   `pre-upgrade-<from>-to-<to>.zip` beside it. The nightly backup replaces the latest copy every night; this one
   stays until the next upgrade. `~/.cherrypick/state/upgrade.json` records the old version, the old
   folder and commit, the target, the backup and the time.
4. **Fetches the release.** A git checkout fetches the tags. A ZIP install downloads the release and
   unzips it into a **new folder** next to the old one (`cherrypick-v0.13.0`); the old folder is never
   written to.
5. **Stops the suite**, the way `uninstall.cmd` and `run.py stop --all` do.
6. **Installs the new release**: for a git checkout it checks out the tag, then runs the new release's
   own installer in its upgrade mode, which keeps your settings and asks nothing a first install asks (no Dolt
   download, no broker login, no first lists).
7. **Migrates the data**, with the new release's code: every pending migration, in order, a dry run
   first. A migration is idempotent and recorded in `~/.cherrypick/state/migrations.json` as it
   succeeds. Releases prefer a compatibility shim or a `doctor` warning to a migration, so most
   releases have none.
8. **Starts and checks.** It starts the suite and checks `doctor`, the supervisor's job table, the
   console at `/api/health` and the version chip reporting the new release. It prints each result.

If any step fails, it stops there and says which. It does not undo anything by itself; run it again
with `--rollback` when you are ready.

## When it refuses

All reasons are listed together, and nothing has changed when you see them.

- **A live loop is armed for today, or a live position or order is open.** This is never overridable.
  A loop that is mid-session, or a book it cannot read, is not something to stop and restart. Wait for
  the position to close, or close it yourself, and for the arm to lapse.
- **Outside the quiet window.** The window is derived from the job table: it starts when the last session
  job ends (the live loops, the streamer, the alert watchers) and ends when the first fixed-time job of the
  morning begins, so it follows your config. A weekend or a market holiday is quiet all day. The refusal
  prints the window for your config. `--force-window` waives this check and nothing else.
- **Not enough free disk.** A ZIP install needs room for a second copy with its own environment.
- **A checkout with uncommitted changes**, a development checkout past its tag (those update with git),
  or a ZIP whose new folder already exists.
- **The Windows service is installed.** The service restarts a stopped supervisor, so the script cannot
  stop the suite. Stop the service first (`run.py service status` prints the steps) and run it again.
- **The target has no manifest**, which means it predates this tooling (see below).

## Rolling back

```bash
upgrade.cmd --rollback           # Windows; on macOS and Linux: ./upgrade.sh --rollback
```

The rollback has the same live and window checks. It stops the suite, saves a snapshot of the data as it
is now (`backups/pre-rollback`), restores the pre-upgrade backup over the data, brings the old code back
(`git checkout` of the recorded commit and a rebuild, or the old folder, which a ZIP upgrade left alone),
starts it and checks it.

The data is restored from the backup, and not left as it is, on purpose. Ledgers migrate forward when a
newer release opens them, so old code must never read data the new code has touched. The cost: what
the suite recorded after the upgrade is not in the live home any more. It is in the snapshot.

The old folder of a ZIP upgrade stays until you delete it; the rollback needs it.

## Moving from v0.11 to v0.12 by hand

v0.11 has no upgrade script, so this one move is manual. From v0.12.0 on, `upgrade` does it.

1. Stop the suite (`uninstall.cmd` or `./uninstall.sh`; settings and history are kept).
2. Run `backup` and note where it wrote (`python packages/orchestrator/run.py backup`).
3. Get v0.12.0 from the [releases page](https://github.com/joncovington/cherrypick/releases): unzip it
   into a new folder, or in a git checkout run `git fetch --tags origin` and `git checkout v0.12.0`.
4. Read the v0.12.0 notes in the changelog on that page for anything you have to do.
5. Run the installer in the new folder (`install.cmd` or `./install.sh`). It keeps your settings, finds
   your broker login and starts the suite.

## What a release carries

Each release publishes a small manifest with its notes: what a person upgrading should know, steps to do
by hand, config keys it changed, changes that move a measured number (so a series breaks at that release)
and the ids of its data migrations. `upgrade` reads the target's manifest from the release page before it
downloads anything, so the notes in front of you are the target's own.

## Related

- [System and Config](system-and-config.md): where the suite's health and settings live.
- [FAQ](faq.md#how-do-i-upgrade): the short answer.
