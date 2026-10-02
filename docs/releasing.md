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

Every PR with a change a user would notice adds a line under `## [Unreleased]` in `CHANGELOG.md`,
saying what changed and anything a user has to do. That section is the next release's notes, so it is
written for the person upgrading, not as a commit log.

## Cutting a release

1. **Check `main` is green.** The latest CI run on `main` passes, and nothing half-finished is
   waiting behind a feature switch that ships on.
2. **Name the release in the changelog**, in a PR of its own. Rename `## [Unreleased]` to
   `## vX.Y.Z — YYYY-MM-DD — <a short name>` (the date and the name follow em dashes, as every
   earlier heading does), and put a fresh, empty `## [Unreleased]` above it. Merge it.
3. **Tag the merge commit on `main`** and push the tag:

   ```bash
   git fetch origin
   git tag -a vX.Y.Z origin/main -m "vX.Y.Z — <the short name>"
   git push origin vX.Y.Z
   ```

4. **The Release workflow publishes it** ([`.github/workflows/release.yml`](../.github/workflows/release.yml)).
   It refuses a tag that is not on `main`, cuts that version's section out of the changelog with
   [`tools/release_notes.py`](../tools/release_notes.py) (and refuses a tag whose section is
   missing, undated or empty), then creates the GitHub Release with the section as its notes. GitHub
   attaches the source ZIP and tarball itself.
5. **Check the result**: the release is at the top of the Releases page with its notes, and
   `/releases/latest` opens it.

If the workflow fails, nothing was published. Fix the cause (usually the changelog section), then
move the tag and push it again:

```bash
git push --delete origin vX.Y.Z && git tag -d vX.Y.Z
# fix, merge, then tag and push as in step 3
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
  leaves git in "detached HEAD" at the tag, which is expected. To upgrade later: stop the suite,
  `git fetch --tags origin`, the same `git checkout` line, then run the installer again.
- **Developers** clone `main` and work there; the per-package setup pages do exactly that.

## Your own machine

The suite on a machine runs whatever its checkout holds: the supervisor, every scheduled job and the
console all run from that folder. A checkout that is also where you develop therefore runs whatever
branch is checked out, unreleased work included. Running the suite from a second checkout pinned to
the latest release, and developing in another, gives a trading machine the same separation users get.
