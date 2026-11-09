# Releasing

`main` is where development happens. A **release** is a tagged commit on `main` that has been
published as a [GitHub Release](https://github.com/joncovington/cherrypick/releases), and it is what
a new install gets: [README.md](../README.md), [INSTALL.md](../INSTALL.md) and
[QUICKSTART.md](../QUICKSTART.md) all point at the latest release, never at `main`. So merging a
feature or a fix changes nothing for someone installing until a release is cut, and a release is cut
only when a batch of work is finished.

## Versions

`vMAJOR.MINOR.PATCH`, as the [changelog](../CHANGELOG.md) already numbers them.

- **Minor** (`v0.11.0`): a finished batch of features and fixes, or an architectural boundary (a new
  package, a scheduler cutover, a read-side or trading-mode change).
- **Patch** (`v0.11.1`): fixes only, to the latest release.
- **Major**: not used yet. `v1.0.0` would mean the suite's config and data layout are promised stable
  across upgrades.

A patch release is cut from `main` like any other, so it carries everything merged since the last
release. If that ever means shipping a fix together with an unfinished feature, the time has come for
release branches (`release/v0.11` from the tag, fixes cherry-picked onto it, patch tags cut there),
and the workflow's "on main" check below would need relaxing to match. Until then there are none.

## While developing

The changelog is written in `## [Unreleased]` as PRs land, not at the end. Every PR with a change a
user would notice adds a line there, saying what changed and anything a user has to do. A
measurement boundary goes in too, with its date. That section is the next release's notes, so it is
written for the person upgrading, not as a commit log.

CI enforces it with a warning: a PR that changes files under `packages/*/src`, `scripts/` or
`install.*` without editing `CHANGELOG.md` gets one
([`tools/check_changelog_touched.py`](../tools/check_changelog_touched.py)). It never fails the
build, since a refactor or a test-only fix needs no line. Label the PR `no-changelog` to skip it.

Before the tag, the notes are rewritten as one `## vX.Y.Z` section in the shape of v0.11.0: a short
narrative, `### Upgrading`, `### What's new` and `### In detail`. `release/manifest.json` gets the same
release's upgrade notes, which `run.py upgrade` shows before it changes anything; keep the two
consistent.

## The release manifest

Every release has an entry in [`release/manifest.json`](../release/manifest.json). It is what
`run.py upgrade` shows a person before it changes anything, so it is written for them:

- `upgrading`: what someone upgrading should know (not empty);
- `manual_steps`: anything they have to do by hand;
- `config_changes`: config keys the release added, renamed or removed, each `{file, key, note}`
  (`/cherrypick-upgrade` checks the person's config against these);
- `measurement_boundaries`: changes that move a measured number, so a series breaks at this release;
- `migrations`: the ids of the data migrations it ships, in the order they run;
- `first_with_upgrade_tooling`: true on the first release that has `upgrade`.

A PR that adds a migration (`orchestrator/migrations/`, whose docstring says to prefer a shim or a
`doctor` warning) adds its id to the entry of the release it ships in, creating the entry if that
release has none yet; a test keeps the registry and the manifest in agreement. The entry's notes are
final text before the tag is cut, usually in the changelog PR.

## Cutting a release

1. **Check `main` is green.** The latest CI run on `main` passes, and nothing half-finished is
   waiting behind a feature switch that ships on.
2. **Retake the guide's screenshots.** With the console built, run `python scripts/reshoot.py`. It
   builds a demo home, shoots every image in `docs/images/shots.json` again and lists the ones that
   changed. Look at each changed image, and commit them in a PR of their own (or with the changelog
   PR) if they are right. ([demo-home.md](demo-home.md) has the rules the demo data keeps.)
3. **Name the release in the changelog**, in a PR of its own. Rename `## [Unreleased]` to
   `## vX.Y.Z — YYYY-MM-DD — <a short name>` (the date and the name follow em dashes, as every
   earlier heading does), and put a fresh, empty `## [Unreleased]` above it. In the same PR, write the
   version's entry in `release/manifest.json` (see above). Merge it.
4. **Tag the merge commit on `main`** and push the tag:

   ```bash
   git fetch origin
   git tag -a vX.Y.Z origin/main -m "vX.Y.Z — <the short name>"
   git push origin vX.Y.Z
   ```

5. **The Release workflow publishes it** ([`.github/workflows/release.yml`](../.github/workflows/release.yml)).
   It refuses a tag that is not on `main`, cuts that version's section out of the changelog with
   [`tools/release_notes.py`](../tools/release_notes.py) (and refuses a tag whose section is
   missing, undated or empty), refuses a tag with no well-formed entry in the manifest
   ([`tools/check_release_manifest.py`](../tools/check_release_manifest.py)), then creates the GitHub
   Release with the section as its notes and the manifest attached as `release-manifest.json`, which
   `upgrade` reads from the release page. GitHub attaches the source ZIP and tarball itself.
6. **Check the result**: the release is at the top of the Releases page with its notes, and
   `/releases/latest` opens it.

If the workflow fails, nothing was published. Fix the cause (usually the changelog section), then
move the tag and push it again:

```bash
git push --delete origin vX.Y.Z && git tag -d vX.Y.Z
# fix, merge, then tag and push as in step 4
```

**Never move a tag once its release is published.** Someone has installed it; a correction is the next
patch version.

## What users run

- **ZIP:** the Releases page, `Source code (zip)` under the latest release's assets.
- **git:** clone, then check out the newest tag on `main`, which is the latest release:

  ```bash
  git clone https://github.com/joncovington/cherrypick.git
  cd cherrypick
  git checkout "$(git describe --tags --abbrev=0 origin/main)"
  ```

  In PowerShell the last line is `git checkout (git describe --tags --abbrev=0 origin/main)`. This
  leaves git in "detached HEAD" at the tag, which is expected. To upgrade later, run `upgrade.cmd` or
  `./upgrade.sh` (v0.12.0 and later; [the guide](guide/upgrading.md)), or by hand: stop the suite,
  `git fetch --tags origin`, the same `git checkout` line, then run the installer again.
- **Developers** clone `main` and work there; the per-package setup pages do exactly that.

## Your own machine

The suite on a machine runs whatever its checkout holds: the supervisor, every scheduled job and the
console all run from that folder. A checkout that is also where you develop therefore runs whatever
branch is checked out, unreleased work included. Running the suite from a second checkout pinned to
the latest release, and developing in another, gives a trading machine the same separation users get.
