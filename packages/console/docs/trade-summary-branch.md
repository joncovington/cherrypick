# The trade-summary redesign branch

Status notes for `console/trade-summary-redesign`, kept on the branch itself so they travel with the
work. If this branch merges to `main`, this file either goes with it or moves to `docs/history/` —
it should not survive on `main` as a living doc.

## 2026-10-10 — the prototype is adopted, and becomes the suite's theme

The MEIC summary slide's borrowed visual language is no longer a prototype under judgement: it is
adopted, and it is being extended across the whole console (this branch never merges, so the
adoption is recorded here and in the styles.css comments, not as a landing note). The decisions,
made together so the rest of the work does not re-litigate them:

1. **The palette is the authority.** The prototype's tokens move to `:root` under new names
   (`--canvas/--surface/--border/--track/--accent/--pos/--neg/--warn/--text/--text-muted/--text-dim`)
   and the old `:root` names survive as aliases onto them until each surface is swept. Two
   disagreements between the prototype and the write-up resolved in the prototype's favour where it
   is the thing being extended: borders read `#262a32` (the slide's drawn border, and `#282c34`
   next to it read as drift, not intent); the grid/track tone reads `#2a2e37` for the same reason.
   Revisit at the chrome step if the darker grid is genuinely wanted.
2. **The cherry accent survives, as `--accent-live: #d23f57`.** The adoption hands the interactive
   accent to blue, but the cherry is doing semantic work blue cannot: it is reserved for brand, for
   the LIVE badge, and for the alert moments — the Config halt control most deliberately ("the one
   control that stops live trading is the one thing that looks urgent"). Those three keep cherry;
   focus rings, selection and link hovers go blue.
3. **Navigation icons are for pages, not slides** (option A). The ~17 top-level links get icons —
   which is what makes the 64px collapsed rail actually navigable (today's 26px strip holds only
   the toggle); the ~70 slide entries stay text. Icons live in the decl data (`navLinks.ts`'s
   `NavLinkDef`, one optional `icon` field), never a parallel hand-kept map.
4. **The light variant is retired from the slide and becomes a global setting.** The
   `meicSummaryLight` toggle and its `.ts-light` token set die with the slide's own scope; a
   single `theme` console pref (mirrored like every pref, so the first render paints the chosen
   theme) carries a light variant of the global palette once the dark sweep is done. Until that
   exists there is no light console — that is a gap, stated here rather than hidden.
5. **Scope controls stay per page.** The write-up's global arm/era/date bar contradicts the
   one-scope-per-page rule and flattens per-module era semantics; deferring it to its own branch,
   where it would be a module-frame refactor across every manifest — not part of this restyle.
6. **Cards still link; nothing opens an overlay.** The write-up's full-screen drill-down is the
   existing ⤢ link to a rail page carrying the query string, never a modal.

Order of work: tokens and the slide's toggle removal first (one commit, the whole suite shifts to
the new canvas/surface/border at once); then the chrome (rail, status bars, ticker); then the
shared components (`GridCard`, `TileGrid`, `PerformanceTab`, `HistoryTable`, `TradeMoney`), which
restyle most of the suite at once; then the per-module hand-rolled SVGs; the `.ts-*` scope dies as
its rules land in the shared vocabulary. Every step is verified in a rebuilt browser
(`pnpm ui-check`), not by tests alone.

## 2026-10-10 — the chrome swept

- The collapsed rail is the 64px icon strip (decision 3, built): one glyph per top-level link in
  `navIcons.tsx`, hand-drawn on the 24px grid, `currentColor` only, and each glyph **is** its
  page's link — the folded rail navigates without expanding, `aria-label` naming each glyph. The
  strip keeps the tab order of the links; the expanded list remains the screen-reader text, and
  `routes.test.tsx`'s collapsed-rail assertions still hold. The expanded
  rail keeps today's structure — its restyle belongs with the card sweep, not the chrome.
- Chips took the slide's own pill (999px) rather than a third corner language, and their tone
  borders derive from `--pos/--neg/--warn` via `color-mix` — 28 hardcoded tone rgba literals are
  now 7 token mixes.
- Two rules written for the cherry accent went blue in the token hoist (the live-mode toggle, the
  review attention card) and are restored to `--accent-live` per their own block comments. The
  lesson is recorded for the remaining sweep: `--accent` means blue now; a rule that says cherry
  in prose must say `--accent-live` in code.
- The menu-selection tint and hover accents stay blue for this step; a deliberate pass over them
  comes with the shared-component sweep, which is when the last of the legacy `--bg*`/`--ok`/
  `--err` aliases leave.

## 2026-10-10 — the shared components swept, and the aliases die

- The slide's own anatomy went suite-wide: cards and panels at 14px (`--surface`), tiles at 10px,
  controls at 8px, every pill at 999px, dropdowns/toasts at 12px on `--surface-raised`. Tables
  keep the slide's clean horizontal rules — hover carries the zebra (`--surface-raised`), not the
  rows; `data-table` heads sit on `--surface-sunken`. Tracks and bars read `--track`, inset
  reading panels sunken.
- The legacy aliases left the stylesheet for good: `--bg`, `--bg-card`, `--bg-raised`,
  `--row-line`, `--row-hover`, `--ok`, `--err`, `--cached` now have zero reads in `styles.css`
  (they stay declared in `:root` for module CSS and the server-side style until the `.ts-*`
  retirement removes their last readers). The tone rgba literals left with them: every
  green/red/amber tint is a `color-mix` over `--pos/--neg/--warn`, so the light theme derives
  rather than re-specifies.
- Third cherry restoration: the halted lock-hero title, like its own border rules, is
  `--accent-live`.

## 2026-10-10 — the chart kit reads the tokens; the hand-SVG greys become a grid

- Three new tokens: `--chart-grid` (the faint line texture, `#15181e`), `--chart-grid-strong`
  (the zero line and hover crosshairs, `#3d4653`), `--chart-grid-mid` (pane separators,
  `#23262d`). Every hand-SVG gridline in the suite now reads one of the three; the six or so
  greys each file guessed for itself collapse onto them.
- The shared palettes went var-first: `ARM_COLORS` leads with `--accent/--pos/--amber-line/
  --chart-special` (the categorical tail stays literal — it is a *set*), `SPOT_COLOR` is
  `--amber-line`, the outcome vocabulary reads the tokens where the theme names one, and
  `AXIS_MUTED_HEX` (its name kept — a dozen call sites) now carries `var(--text-muted)`. SVG
  inherits no custom property, but the *attribute* is read back in the DOM: the var() is
  resolved against the stylesheet, which is exactly what we want for the theme step.
- Canvas knows nothing of custom properties, so the lightweight-charts surfaces (the
  performance cards, ChartPage, IntradayPage) go through one runtime reader,
  `chart/cssvars.ts`: `getHex`/`hexOf` read `:root` once at construction (module-level consts
  use the lenient read — SSR-safe), `resolveColor` turns a caller's `var(--pos)` into the hex
  the canvas needs, and a missing token throws rather than painting a wrong colour. The
  wrappers resolve on the callers' behalf, so a series colour written as a token works the
  same as one written as a hex.
- The inline alias readers (Spark, OccupancyMap, the bar tracks, Earnings bars, Flow) moved to
  canonical names; RegimeCutsTab's rate-tinted cell now derives its tint with `color-mix`
  instead of its own rgba literal. Zero `--ok/--err/--row-line` reads remain in TSX.
- The remaining literals are the resolution seam itself: `hexOf` fallbacks (the value the
  canvas would read before hydration, kept in sync with `:root`) and one luminance threshold
  for chip ink, which is arithmetic, not palette.

## 2026-10-10 — the scope dies: the slide reads the shared palette directly

- The legacy aliases left `:root` for good: `--bg`, `--bg-raised`, `--bg-card`, `--row-line`,
  `--row-hover`, `--ok`, `--err`, `--cached` are no longer declared anywhere. Their last
  readers were the desktop splash (which lifted its palette from an older `styles.css`) —
  ported to the current names and values — and one fallback-comma read (`--row-hover`) the
  exact-paren grep of the shared sweep had missed; both canonical.
- The slide's private namespace went with them: the ten `--ts-*` aliases, the `.ts-root`
  scoping on every descendant rule, and the three undefined-var fallbacks (`--needle`,
  `--neutral-bar`, `--ts-fg`) that only ever resolved to white/inherit because nothing set
  them. `.ts-needle` is `--text`, `.ts-hbar-fill.ts-neutral` is `--chart-neutral` (which is
  what its `#64748b` was reaching for), the pager inherits.
- The `.ts-` class names stay — they are wired in `SummarySlide.tsx` and pinned by its tests;
  what died is the second palette they used to resolve through. Verified computed-identical
  in the browser: card `#1a1d23` at 14px, border `#262a32`, seg-on `#3761e1`, track `#2a2e37`,
  the numeral face — every value the slide now reads straight off the shared tokens.

## 2026-10-10 — the light theme: the prototype's original palette, promoted to a second reading

- The theme is a string pref (`dark`/`light`) on Config's prefs slide, riding the same store
  and mirror as the booleans. `index.html` gained a boot script that reads the mirror before
  first paint and sets `data-theme`, so a saved light choice never flashes dark on its way in;
  the pre-CSS inline background grew a light override to match. The Shell effect keeps the
  attribute in step when the pref arrives from the server — and a mid-session flip reloads,
  both directions, because the canvas palette consts (`chart/cssvars.ts`, the candlestick
  pages) read `:root` at module import: without the reload the CSS-derived surfaces flip and
  the charts keep the old palette. One reload beats half-flipped charts.
- `:root[data-theme="light"]` re-specifies every token the dark block declares — the values are
  the summary slide's original `.ts-light` set, come home: canvas `#f6f8f9`, card white, the
  prototype's own sign tones `#208a56`/`#c2372c` (a mint tuned for charcoal is invisible on
  white — dimming the dark values would have been the wrong move), amber deepened to `#b45309`
  for contrast, the grey ramp derived from the prototype's `#e5e7eb` border. `color-scheme`
  rides along. Nothing below the two blocks names a hex — the sweep's whole point.
- The completeness the block needed was verified, not assumed: fourteen literals survived
  below `:root` (the advisor cells and bars — the shared sweep's own declared leftovers — the
  expected-move band and `chip-em`'s ochre family, the skeleton shimmer's lift pigment, one
  `--surface-2` fallback that was never a token at all). All onto tokens or color-mixes; the
  two whites that remain are ink on saturated-accent fills, theme-invariant by design.
- `test/tokensComplete.test.ts` guards all three seams: no literal below the token blocks (bar
  the two inks), the light block re-specifies exactly the dark set, and the canvas palette's
  reads name tokens the stylesheet declares. Each proven to fire by breaking the thing it
  counts before landing.
- Caught on the way: `CumulativeCard.tsx` was feeding `ARM_COLORS` — three entries of which
  are now `var()` strings — straight into lightweight-charts, which parses colours for canvas.
  The sweep's resolveColor fix had covered the wrappers but missed this direct consumer; its
  series were painting wrong. Fixed in this commit.
- Verified live in the browser: boot dark → toggle light (attribute, inline background, card
  white, `--pos #208a56`, reload re-derives the canvas consts) → fresh load boots light from
  the mirror → back to dark, everything restored. 305 tests (the routes mock learned `getPref`),
  typecheck, build green.

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
