---
description: Upgrade cherrypick to a newer release - explains the notes in plain words, checks your config against the changed keys, then runs the upgrade script
argument-hint: [--to vX.Y.Z] | [--rollback] | [--dry-run]
---

Move this cherrypick install to a newer release. The work is done by the orchestrator's `upgrade` command
(the root `upgrade.cmd`, `upgrade.ps1` and `upgrade.sh` call the same thing); this command explains
what the release asks of me first, checks my settings against it, and then runs the script. The script
is the source of truth, so never do any of its steps by hand: not the stop, not the install, not the
migrations, not the restart. Use the checkout's `.venv` interpreter for every command, from the repo root.
`docs/guide/upgrading.md` is the full description.

Do this:

1. **Preflight, changing nothing.** Run the orchestrator's `upgrade` with `--dry-run`, passing along
   `--to` if I gave one. It prints the installed and target versions, then either a list of reasons it
   will not run, or the target release's notes. If I asked for `--rollback`, jump to the rollback section
   below.

2. **If it refused, explain and stop.** Say each reason in plain words.
   - A live loop armed for today, an open live position or order, or a live book it could not read: these
     are never overridable. Do not look for a way around them. Tell me what is open and that the upgrade
     waits until it is closed and the day's arm has lapsed.
   - Outside the quiet window: say the window the refusal printed and when it opens next. Offer
     `--force-window` only as something I may choose, never use it on your own, and remind me it waives
     the window and nothing else.
   - Low disk, a dirty checkout, an existing target folder, or the Windows service: say what to do about it.
   - A target with no manifest, or a v0.11 install: this tooling cannot do that move; point me at the
     manual steps in the upgrading guide.

3. **Explain the notes in plain words.** From the dry run's output, tell me in a few short paragraphs: what
   the release changes for me, anything I have to do by hand, which data migrations will run and what
   each does to my data, and any measurement boundary (a change that moves a measured number, so a
   series breaks at this release and results before and after are not comparable). If I am skipping
   releases, cover each one. Do not paste the raw notes back.

4. **Check my config against the changed keys.** For each config key the notes name (the file and the
   key), look in my files under the cherrypick home: the suite config and the per-module configs. Say which
   of those keys I actually use and what to change, in my own terms. Never print a secret, a webhook URL or
   a full account number; mask accounts to the last four digits. Do not edit my config: changes go through
   the Config page or the settings editor, and only if I ask.

5. **Ask for my go-ahead.** Wait for a clear yes in this chat. Then run `upgrade` for real with `--yes`
   (my yes here is the confirmation the script would otherwise ask for), with `--to` if I gave one. It
   takes several minutes: it backs up, fetches, stops the suite, runs the new release's installer,
   migrates, starts and verifies. Never add `--force-window` unless I said to.

6. **Report.** Tell me what the script printed: the verification results (doctor, the job table, the console
   health, the version chip), the path of the pre-upgrade backup, and for a ZIP install the old folder that
   is safe to delete once I am happy. If a step failed, say which, say that the suite may be stopped, and
   explain that `--rollback` puts the old release and the backup back. Do not roll back on your own: ask me.

**Rollback.** If I asked for `--rollback`, explain what it will do before running it: it saves a snapshot of
the current data, restores the pre-upgrade backup over it (ledgers migrate forward, so old code must never
read data the new code has touched, which means anything recorded since the upgrade leaves the live home
and stays in the snapshot), brings the old code back and restarts. It has the same live and window refusals.
Run it with `--dry-run` first, wait for my yes, then run it with `--rollback --yes`.
