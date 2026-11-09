Switch MEIC's LIVE configuration to a named arm from this machine's arm registry, backing up the config
first.

## Overview

An arm bundles entry-gate thresholds with offsetting position-cap and stop-management constraints.
`/set-risk-profile moderate` (for example) reads the named arm from this machine's registry, backs up
MEIC's config, overwrites the relevant keys, and reports what changed, **without restarting the loop**.
The new settings take effect on the next iteration.

Where the files are (MEIC's own resolvers, so this command and the loops never disagree):

- **The registry**: `cherrypick.meic.paths.risk_profiles_path()`: `$MEIC_RISK_CONFIG`, else
  `~/.cherrypick/config/meic.risk.json`, else the shipped control-only `config.risk.example.json`.
- **The config it edits**: `cherrypick.meic.paths.config_path()`: `~/.cherrypick/config/meic.json`.

> The four example ladder tiers (`conservative`, `moderate`, `aggressive`, `very-aggressive`) are **not
> shipped**. A base install's registry holds only `control`. The ladder exists only in a registry you
> have built yourself; see [docs/risk-profiles.md](../../docs/risk-profiles.md). Never point this
> command at a paper sampling stream (`control`, `live-shadow`, `bp-*`, ...): those are paper arms, not
> risk-appetite presets, and their settings only make sense as independent paper samples.

> This changes what MEIC's **live** path would trade. Live trading is experimental and off by
> default; see `DISCLAIMER.md` at the repo root.

> Since 2026-10-14 the way to choose what live trades is `live.arm` in the `live` block, naming a
> registry arm (disabled for paper). Writing an arm's keys into the config's top level changes every
> paper arm that leaves a key unset, `control` included, and any key the `live.arm` arm sets still
> wins over it. Prefer adding the preset to the registry and naming it in `live.arm`; live-only sizing
> belongs in the `live` block.

## What to do

This file holds no code; it names the tools.

1. **List the arms the machine has.** Open the registry file named above and read its `profiles` keys
   (the key is the historic spelling of `arms`) and `active_profile`. If the registry is the shipped
   `config.risk.example.json`, this machine has no registry of its own yet and only `control` exists:
   there is nothing to switch to. Copy the example to `~/.cherrypick/config/meic.risk.json` and add
   arms there first.
2. **Refuse what is not there.** If the name the user gave is not a key in the registry, stop and list
   the names that are. Never write into the shipped example files; a fresh install runs the shipped
   `config.example.json` read-only, so start the machine's own `~/.cherrypick/config/meic.json` from it
   first.
3. **Make the change through the config editor, not by hand.** Use the console's Config page, or the
   orchestrator's config editor it calls (`python -m cherrypick.orchestrator.configcli`, JSON on stdin).
   It backs the file up beside itself, keeps the file's `_note` text, and refuses the guarded live
   pointers. Set `live.arm` to the chosen arm; if the user really wants the keys at the top level, set
   those keys one by one and show the old and new values in a table.
4. **Moving live-only keys out of the top level** (the case the 2026-10-14 split was for) is
   `python -m cherrypick.meic.live_migrate`: a dry run that prints the plan and every paper arm whose
   effective value would change, then `--apply` to write both files, each backed up first.
5. **Verify.** Read `min_iv_rank`, `max_concurrent_ics` and `live.arm` back from the config and report
   them with the registry's `active_profile`.
6. **Revert.** Restore the backup beside `meic.json` (a `.bak` or the editor's timestamped copy), or
   switch back by name.

## Summary to give the user

- **Switched to**: the arm name
- **Keys changed**: a table of old and new values
- **When it takes effect**: the next loop iteration (resident loops read config at start, so a
  restarted job is needed for any that were already running)
- **To revert**: restore the backup, or switch back by name
- **Details**: [docs/risk-profiles.md](../../docs/risk-profiles.md)
