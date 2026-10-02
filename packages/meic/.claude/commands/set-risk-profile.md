Switch MEIC's LIVE configuration to a named risk profile from this machine's arm registry, backing up
the config first.

## Overview

A risk profile bundles entry-gate thresholds with offsetting position-cap and stop-management
constraints. `/set-risk-profile moderate` (for example) reads the named profile from this machine's
arm registry, backs up MEIC's config, overwrites the relevant keys, and reports what changed —
**without restarting the loop**. The new settings take effect on the next iteration.

Where the files are (MEIC's own resolvers, so this command and the loops never disagree):

- **The registry**: `cherrypick.meic.paths.risk_profiles_path()` — `$MEIC_RISK_CONFIG`, else
  `~/.cherrypick/config/meic.risk.json`, else the shipped control-only `config.risk.example.json`.
- **The config it edits**: `cherrypick.meic.paths.config_path()` — `~/.cherrypick/config/meic.json`.

> ⚠️ **The four ladder tiers (`conservative`, `moderate`, `aggressive`, `very-aggressive`) are not
> shipped.** A base install's registry holds only `control`. The ladder exists only in a registry you
> have built yourself; see [docs/risk-profiles.md](../../docs/risk-profiles.md). Never point this
> command at a paper sampling stream (`control`, `live-shadow`, `bp-*`, …): those are paper arms, not
> risk-appetite presets, and their settings (`overlap_scope: "none"`, no per-side stop) only make sense
> as independent paper samples.

> ⚠️ This changes what MEIC's **live** path would trade. Live trading is experimental and off by
> default; see `DISCLAIMER.md` at the repo root.

## Step 1 — List the profiles this machine has

```bash
python -c "import json; from cherrypick.meic.paths import risk_profiles_path as p; f = p(); cfg = json.load(open(f, encoding='utf-8')); print('Registry:', f); print('Profiles:', ', '.join(k for k in cfg['profiles'] if not k.startswith('_'))); print('Active profile:', cfg.get('active_profile'))"
```

If the registry printed is `config.risk.example.json`, this machine has no registry of its own yet and
only `control` exists: there is nothing to switch to. Copy the example to
`~/.cherrypick/config/meic.risk.json` and add profiles there first.

## Step 2 — Apply the profile

Replace `<profile_name>`. The script refuses a name the registry does not hold, refuses to write the
shipped example, and backs the config up to `meic.json.bak` beside it before changing anything.

```python
import json
import shutil

from cherrypick.meic.paths import config_path, risk_profiles_path

reg_path = risk_profiles_path()
if reg_path.name == "config.risk.example.json":
    raise SystemExit("This machine has no arm registry of its own (only the shipped example). "
                     "Copy it to ~/.cherrypick/config/meic.risk.json and add profiles first.")
registry = json.loads(reg_path.read_text(encoding="utf-8"))

profile_name = "<profile_name>"  # e.g. "moderate"
if profile_name not in registry["profiles"]:
    raise SystemExit(f"No profile {profile_name!r} in {reg_path}. "
                     f"Profiles: {', '.join(k for k in registry['profiles'] if not k.startswith('_'))}")

profile = {k: v for k, v in registry["profiles"][profile_name].items() if not k.startswith("_") and k != "enabled"}
note = registry["profiles"][profile_name].get("_note", "(no description)")

cfg_path = config_path()
backup = cfg_path.with_name(cfg_path.name + ".bak")
shutil.copy(cfg_path, backup)
print(f"Backed up {cfg_path} -> {backup}")

config = json.loads(cfg_path.read_text(encoding="utf-8"))
changes = {k: (config.get(k), v) for k, v in profile.items() if config.get(k) != v}
config.update(profile)
cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

registry["active_profile"] = profile_name
reg_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"\nApplied profile: {profile_name}\n  {note}\n")
if changes:
    print("| Key | Old Value | New Value |\n|---|---|---|")
    for key, (old, new) in sorted(changes.items()):
        print(f"| `{key}` | {old} | {new} |")
else:
    print("(No keys changed: already at this profile.)")
print("\nThe next loop iteration picks up the new settings (no restart needed).")
```

## Step 3 — Verify

```bash
python -c "import json; from cherrypick.meic.paths import config_path, risk_profiles_path; c = json.load(open(config_path(), encoding='utf-8')); r = json.load(open(risk_profiles_path(), encoding='utf-8')); print('min_iv_rank:', c.get('min_iv_rank'), '| max_concurrent_ics:', c.get('max_concurrent_ics'), '| active profile:', r.get('active_profile'))"
```

## Step 4 — Revert if needed

Restore the backup Step 2 wrote (`meic.json.bak` beside `meic.json` in `~/.cherrypick/config/`), or run
`/set-risk-profile` again with the previous profile name.

## Summary

- **Switched to**: `<profile_name>`
- **Keys changed**: the table above
- **When it takes effect**: the next loop iteration
- **To revert**: restore `meic.json.bak`, or switch back by name
- **Details**: [docs/risk-profiles.md](../../docs/risk-profiles.md)
