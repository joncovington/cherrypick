# The trade-summary redesign branch

Status notes for `console/trade-summary-redesign`, kept on the branch itself so they travel with the
work. If this branch merges to `main`, this file either goes with it or moves to `docs/history/` —
it should not survive on `main` as a living doc.

**2026-10-10** — written when the branch stood at `aaf94a90` (entries pagination) plus the merge of
`origin/main` at `e6dc3ed6`.

## What the branch is

A redesign of the console's MEIC trade-summary slide, run as a local-only branch off `main`. It
replaces the old two-read slide with a single one-read payload (`/api/meic/summary`,
`readMeicSummary`), and every figure on the page is presentation arithmetic over that payload:

- the money row (net / capture gauge / win-rate rings), computed server-side to the cent;
- geometry and side cards in the module's own vocabulary;
- the refusal ledger (evaluations) and the by-arm comparison;
- the by-entry-time card — per-trade bars over an evidence strip of entry counts, the two-row form;
- the by-weekday card — the week as a calendar, Mon→Fri zero-filled, a real zero baseline so a
  danger day drops beneath it, counts and win % as pills, volume deliberately not encoded;
- the entries card — one settled page of 40 with `pages`/`page` in the payload, `?page=` riding the
  URL beside `?range=`, the pager reading the payload's resolved page.

Reconciliation is pinned server-side (`test/meic-summary-reconcile.test.ts`): the arms' nets must
sum to the hero to the cent, with the residual plugged into the largest-magnitude row. Web tests
cover the slide statically (302 across the package at the time of writing).

The branch deliberately holds **no measurement change**: it is a read surface. When it eventually
lands, the landing note goes in the console's own history, not here.

## Keeping current with main

The branch merges `origin/main` early and often — small, frequent merges keep conflicts tiny, and
`styles.css` and `SummarySlide.tsx` are exactly the files future main-side console work will also
touch. Merge, never rebase: the branch is unpushed, so history rewrites buy nothing and cost
conflict surface.

### The hourly sync (this machine only)

A systemd user timer runs a fetch+merge script every hour:

| Piece | Path |
|---|---|
| Script | `~/.local/bin/cherrypick-sync-main` |
| Service | `~/.config/systemd/user/cherrypick-sync-main.service` |
| Timer | `~/.config/systemd/user/cherrypick-sync-main.timer` (hourly, ±2 min, `Persistent=true`) |
| Log | `~/.local/state/cherrypick-sync-main.log` (self-truncating) |

Manual run and control:

```bash
~/.local/bin/cherrypick-sync-main          # same run the timer does
git sync-main                              # repo-local alias: fetch + merge --no-edit
systemctl --user disable --now cherrypick-sync-main.timer   # pause
systemctl --user enable --now cherrypick-sync-main.timer    # resume
```

After rebuilding this machine (or cloning the setup elsewhere), restore it with:

```bash
systemctl --user enable --now cherrypick-sync-main.timer
```

assuming the three files above exist; the script takes `CHERRYPICK_SYNC_REPO`, `CHERRYPICK_SYNC_BRANCH`
and `CHERRYPICK_SYNC_LOG` env overrides, which is how its guards were tested.

### The script's rules (each verified by breaking it on purpose, against throwaway clones)

1. **Never pushes.** The branch stays local until a human says otherwise. This is the load-bearing
   one: the redesign is not offered upstream while it is under development.
2. **Merges only the declared branch** — checked out as anything else, it skips. A mid-session
   checkout is left alone.
3. **Never merges over a dirty tree** — uncommitted work blocks the merge and raises a notification.
4. **Never rebases** — a rebase would rewrite unpushed history and manufacture conflicts.
5. **Fetch failure is a skip, not an error** — offline is normal; the next tick retries.
6. **A conflicted merge aborts itself** — `git merge --abort`, tree left clean, critical
   notification. Conflicts are resolved by hand, never automatically.

## When the branch is ready to be seen

Options, in increasing exposure — all still open while the branch merges main cleanly:

1. **Keep it local** (current posture). No push, no PR; the only cost is merge cadence, which the
   timer handles.
2. **Push the branch without a PR** — visible but clearly not proposed; `console/…`-named, not an
   offer.
3. **Draft PR** (`gh pr create --draft`) — explicitly not-for-merge, CI runs, the diff is readable
   without being actionable.
4. **Real PR** — at which point the landing note moves to the console's own docs and this file goes
   to `docs/history/` or dies with the branch name.
