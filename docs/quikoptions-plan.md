# QuikOptions: hot options and the event calendar — plan

*Drafted 2026-10-02. A build plan, nothing built yet. Two pages of app.quikoptions.com, read with
our own login: the Hot Options Report (`/Market/Options/THOR/Stock`), shown on its own console page
and posted to Discord as a daily series on its own webhook, and Resources > Calendars as an
independent check on our own event calendar.*

## Why

**Hot options.** The overview's `hot_options` block (`packages/overview`, `occ.py`) ranks
underlyings by OCC's cleared volume. OCC says how much traded; it does not say how. The QuikOptions
report adds what OCC cannot: trade counts by size bucket, the day's largest single-leg trades,
sweeps, spreads, and volume against open interest. It is also the nearest free thing to the
"large options trades" section that `docs/market-report-plan.md` deferred as paid data (gap 1,
Phase 6). It is not that section: it is a top-ten list per table, not the full tape, and whether
it says bought or sold is for the probe to find out (the coloured dot beside each sweep's price
may be an at-bid/at-ask marker; until that is confirmed, nothing reads it as one).

**The calendar.** `cherrypick.core.events` builds each session's releases from primary sources
(BEA, Census, FRED, the curated FOMC days, and rules). Two wrong 2026 FOMC dates (#17) and FRED's
mixed release ids (#19) were each found by hand. A second calendar, compared mechanically, finds
the next one. It is a **check, never a source**: nothing it says is folded into our calendar, and a
disagreement is settled at the agency's own page.

## Posture

The site requires an account but no payment, so it is named openly in the code, config and docs
(decided 2026-10-02), unlike the vendor collector (`scripts/fetch_vendor_edition.py`), whose
subscription site stays out of the code. Everything else follows that collector:

- **A script, not package code.** It reaches the network, so it lives in `scripts/`, writes only
  its own store `~/.cherrypick/data/quikoptions/`, and a failure leaves every saved day as it was.
  Packages read the store and never the site.
- **A person's pace.** One browser session per run, one load of the report a day after the close,
  a dwell and a slow scroll on the page, reached through the menu as a person would. The calendar
  rides along once a week in the same session. No request is sent that the page itself would not
  send: data is read from the responses the page receives.
- **A real browser, presented honestly.** The installed Chrome (Playwright's `chrome` channel) on
  a persistent profile the user signed in to by hand. No stealth plugins, no fingerprint spoofing,
  no captcha solving, no rotating addresses — they are fragile, they are working around the site
  rather than reading it, and being caught costs the account.
- **Stops rather than fights.** A 401, 403 or 429 on an authenticated page, a captcha, or the
  sign-in page ends the run with nothing saved, a notification, and a 24-hour cooldown that later
  runs respect. It never retries a sign-in. A missed day can be fetched by hand.
- **A capture proves itself before it counts** (below). One that fails is kept aside as
  `.rejected`, never as the day's file. A saved day is never overwritten.
- **Credentials.** None by default: the session lives in the browser profile. Auto sign-in is
  optional and, if wanted, uses the OS keyring (service `cherrypick-quikoptions`). Nothing in the
  repo, the config, or a fixture carries a cookie, a token, or the account's identity.
- **Terms.** The terms of service do not prohibit automated viewing (read by the user,
  2026-10-02). The Discord series republishes their figures, which is a separate question from
  viewing: the terms are re-read for redistribution before the series is switched on, and every
  post names QuikOptions as the source.

## Phase 0 — probe (one visible session, a person present)

`python scripts/fetch_quikoptions.py probe` opens Chrome visibly on the profile. The person signs
in. The probe loads the Hot Options Report, waits, scrolls slowly to the footer, clicks Birdseye's
Trades / Volume / Premium in turn, then — after a 20-45 s pause — opens Resources > Calendars
through the menu and steps through each calendar tab it finds. Every response (URL, status, content
type, body) is written to `~/.cherrypick/data/quikoptions/probe/<timestamp>/`, with request headers
dropped.

What the probe has to settle, written back into this document before Phase 1:

| Question | Why it matters |
|---|---|
| Is each table a JSON response, a websocket/SignalR stream, or server-rendered HTML? | Response capture vs DOM parsing |
| One endpoint per table, or one for the page? | What a complete capture is |
| Do the lower tables load only on scroll? | Whether the scroll is required, not just courtesy |
| Do Trades / Volume / Premium each fetch, or re-sort one payload? | Whether a click is needed |
| How do the date picker and the Stock / ETF / Index choice appear in requests? | Session date for validation; scope |
| What do the coloured price dots and the Spread price sign mean? | Whether any side (bid/ask, credit/debit) can be read |
| Which calendars exist (economic, earnings, dividends, holidays, expirations)? | What the comparison can cover |
| How far ahead does each show; are times given, and in which zone? | The comparison window; time checks |
| Session lifetime: does the profile stay signed in across days? | Whether auto sign-in is needed at all |

Scope for Phase 1 is the **Stock** market tab and the ten rows each table shows. The other tabs
(ETF, Index) and the arrows' full lists are follow-ons, each a second request a person would make,
so each is a decision, not a default.

## Phase 1 — the hot-options capture

`scripts/fetch_quikoptions.py`:

| Command | Does |
|---|---|
| `probe` | Phase 0 |
| `login` | Visible Chrome on the profile; a person signs in; the session is saved |
| `credentials` | Optional: store username/password in the keyring for auto sign-in |
| `hot-options [--headed] [--no-jitter]` | The daily capture (and the weekly calendar, Phase 2) |
| `validate FILE...` | Re-run the checks over saved files, offline |

A scheduled run starts after a random 0-25 minute delay, loads the report, waits for every table's
response, dwells 30-60 s with a slow scroll, and saves `hot-options/YYYY-MM-DD.json`: the raw
responses verbatim plus the parsed tables, the capture time, and the session date the page states.

**The checks, each an identity the page already satisfies** (verified on the 2026-10-02 screenshots):

- Birdseye: the size buckets sum to Total (TSLA: 762.4K against 762.5K, to display rounding), and
  Calls + Puts = Total (462.9K + 299.6K = 762.5K). Checked on raw numbers where the payload has
  them, with a rounding tolerance only where it does not.
- Spreads and sweeps: Premium = |price| × size × 100 (AI: 0.47 × 41,900 × 100 = 1,969,300).
- Vol/OI: V/OI = Volume ÷ OI (SPCX: 135,071 ÷ 119 = 1,135.05); the Openings table has OI = 0.
- The session date the page states is the session being saved, and every table is present and
  non-empty.

**Browser mode.** Headed real Chrome is the most faithful presentation; a window opening on the
desktop at 16:45 is the cost. The default is headed with the window started minimised; a config
switch drops to headless with the matching Chrome user-agent, the vendor collector's mode.

## Phase 2 — the calendar check

**Capture.** Inside the daily session, when the newest calendar capture is six or more days old:
after a 20-45 s pause, Resources > Calendars through the menu, each relevant tab, and "next" at
most a few times to cover about four weeks ahead. Saved as `calendar/YYYY-MM-DD.json`, one per
fetch, never overwritten, so their own revisions show up as differences between fetches.

**Comparison** — a pure function in `cherrypick.core.events` over the saved file and our stores,
no network:

- An alias table maps their release names to our labels (CPI, NFP, PCE, GDP, FOMC, Retail, ISM,
  ...). An unmapped name is reported as unmapped, never dropped: a dropped row reads as agreement.
- For each date both sides cover: **match**, **date mismatch**, **time mismatch**, **only theirs**,
  **only ours**, and days where our side is `unknown` (a gap in our sources, not a disagreement).
- Depending on what the probe finds, the same shape against our other calendars:

  | Their calendar | Ours |
  |---|---|
  | Economic releases | `cherrypick.core.events.day_events` |
  | Market holidays, early closes | `cherrypick.core.calendar` |
  | Earnings dates | the earnings module's data |
  | Ex-dividend dates | `scripts/fetch_dividends.py`'s store (calendars and pmcc skip ex-dividend weeks) |
  | Monthly expiries | `RULE_EVENTS` |

`python scripts/fetch_quikoptions.py check-calendar` prints the result; the weekly capture notifies
only when something disagrees. Their calendar can be the wrong one: each disagreement is settled at
the agency's own page, and a correction on our side goes through `backfill-events --restamp`.

## Phase 3 — the console page

The console renders; the Discord series is pictures of what the console renders, so there is one
rendering of the data and two places to see it.

- **Reader** `server/src/readers/quikoptions.ts`: read-only over `~/.cherrypick/data/quikoptions/`,
  validated captures only (a `.rejected` file is never shown). An absent store returns the usual
  empty shape with `degraded: {reason}`, so "never captured" and "reader broken" are told apart.
  Endpoints for a session's tables, the list of captured sessions, and the latest calendar check.
- **Types** in `shared/src/types/quikoptions.ts`; the Python-to-TypeScript boundary re-spells to
  British identifiers as usual.
- **Page** `web/src/pages/HotOptions/` at `/hot-options`, with a session picker: one `GridCard` per
  table — Birdseye, Top outrights, Top sweeps, Top spreads, Vol/OI (OI > 100), Vol/OI (openings) —
  each titled with the session date (`Top sweeps — 2026-10-02`), so a capture can be checked
  against the day it claims. A seventh card, Calendar check, lists the latest comparison's
  disagreements and unmapped names, and says "no disagreements" only when the comparison ran.
- **Off means hidden.** The page and its nav entry appear only when `quikoptions.enabled` is on; a
  direct URL while off renders `ModuleOffCard`, as for any other switched-off feature.
- **Money layout.** Premium is whole-position dollars. A spread's price carries `cr` / `db` only once
  the probe confirms what the site's sign means; until then it is shown as the site gives it, and
  labelled so. Sizes are contracts; Birdseye counts are trades, and say so.
- **Morning page**: a compact block from the overview (top five by trades and the largest sweep),
  linking to `/hot-options`.
- **Overview**: the block beside `hot_options`, read from the store for the newest session before
  the pack, with a `reason` when there is no capture. It is **not a gate**: it can never move the
  phase, so a missed capture costs a section and nothing else.

## Phase 4 — the daily Discord series

`scripts/quikoptions_post.py` — a script, not a package (it drives a browser and pushes a webhook,
so a failure costs a post and never a capture), modelled on `scripts/flies_payoff_post.py`.

**Its own webhook, in the keyring.** Stored under the notification stack's existing service
(`cherrypick-notify`), entry `discord_quikoptions_webhook`, set with
`cherrypick secrets-set --channel discord_quikoptions`. `notify/secrets.py` gains a set of
*dedicated* webhooks beside `SUPPORTED`: `secrets-set`, `secrets-delete`, `doctor` and the status
view accept them, but they are not push channels — they cannot be listed in `notify.channels`, so
suite alerts can never land in the hot-options channel. **No fallback**: if the dedicated webhook
is not set, the run posts nothing and says why. Posting the series to the suite's general Discord
channel instead would be worse than not posting.

**The series**, one post per message, in this order, a few seconds apart (and honouring any 429's
`retry_after`):

| # | Post | Picture | Caption |
|---|---|---|---|
| 1 | Header | none | `<post_title> — Thu 2 Oct 2026 (stocks). Source: QuikOptions, captured 16:52 ET.` |
| 2 | Birdseye | the Birdseye card | top three by trades, with calls/puts split |
| 3 | Top outrights + top sweeps | both cards, one message | largest outright and largest sweep premium |
| 4 | Top spreads | the spreads card | largest by size |
| 5 | Vol/OI | both Vol/OI cards, one message | the highest V/OI name in each |

Pictures come from the console page via `tools/ui-check.mjs --card`, which refuses rather than
crops the wrong card, and the card titles carry the session date, so a page still showing another
day fails the run instead of posting it under today's caption (the payoff post's rule). Captions are
built from the saved capture, never from the picture.

**The title is configurable** (`quikoptions.post_title`, default `Hot options`). It replaces only the
leading name: the session date, the market tab, the source and the capture time are always appended
by the script, so no title can drop the attribution or the date. It is trimmed, must be non-empty,
single-line and at most 80 characters, or the run refuses and says why rather than posting under a
broken header. Every post is sent with Discord's `allowed_mentions` emptied, so a title (or any
caption) containing `@everyone`, `@here` or a role mention renders as text and pings no one. A
title change takes effect on the next session's series; a series already part-posted finishes
under the title it started with (recorded in the marker), so one day never carries two titles.

**Once per session.** A marker in `state/quikoptions-post.json`, written only by this script and only
after a message lands, records which posts of which session went out. A re-run posts only what is
missing, in order, so a failure halfway through resumes rather than repeating posts 1-2. It posts only from a
capture that passed validation; with no capture for the session it posts nothing (a missed day is
not announced to the channel — the collector's own failure already notified the suite's channel).

    python scripts/quikoptions_post.py [--session YYYY-MM-DD] [--dry-run] [--force] [--keep DIR]

`--dry-run` captures and prints the captions without posting; `--keep` saves the pictures.

**What does not go to this channel:** collector failures, cooldowns and calendar disagreements.
Those are operations, and go to the suite's normal `notify` channels.

## Phase 5 — scheduling and config

- `orchestrator/config.py`: `quikoptions_settings(cfg)`, resolving a top-level `quikoptions` block —
  `enabled` (default `false`: a job that fails daily for want of a sign-in is noise), `at` (default
  `16:45` ET, after the page's final results), `headed` (default `true`), `post` (default `false`,
  and it needs `enabled`), `post_at` (default `17:40`, after the latest a jittered capture can
  finish), `post_title` (default `Hot options`).
- `orchestrator/jobspec.py`: two daily jobs, trading days only, each with its `CATCHUP_MINUTES`
  entry — `quikoptions` (`fetch_quikoptions.py hot-options`) and `quikoptions-post`
  (`quikoptions_post.py`). The calendar is weekly by the script's own six-day rule, so no weekly job
  kind is needed. The jobspec comments state the pacing, as the vendor collector's does.
- `config.example.json`: the block with a `_comment` (what it fetches, the pacing, the cooldown, how
  to sign in, how to store the webhook, the terms note), and a row in
  `docs/configuration-and-storage.md`.
- The console's Config page: `quikoptions.post` and `quikoptions.post_title` join the allow-list, so
  the series can be paused or retitled from the console without touching the capture. The title
  is validated by the orchestrator's config editor with the same rule the script applies, so the
  console cannot save one the script would refuse. `enabled` stays out of it: turning the capture on
  needs a sign-in, which the console cannot do.

## Guards, each shown to fail

- **Validation:** a fixture with one Birdseye bucket altered, one premium off by a contract, and one
  table missing — each rejected for the right reason.
- **Calendar comparison:** our side seeded with CPI moved a day, a release removed, and a time
  changed, plus their side with an unknown name — each reported in its own category; and an
  `unknown` day on our side reported as a gap, not a mismatch.
- **Stops:** a 429 after sign-in starts the cooldown; a 403 before a successful sign-in does not
  (the vendor collector's 2026-09-30 lesson); a captcha ends the run with nothing saved.
- **Config:** jobs absent while `enabled` is false; present when true; the post job absent while
  `post` is on but `enabled` is off (`test_jobspec.py`).
- **Webhook:** `notify.channels` containing `discord_quikoptions` is refused; with only the general
  Discord webhook stored, the series posts nothing and names the missing entry.
- **Series:** a failure after post 2 leaves the marker at 2, and the re-run sends 3-5 only; a capture
  whose session differs from the card titles posts nothing; a `.rejected` capture posts nothing.
- **Title:** an empty, multi-line or 81-character title is refused by both the script and the
  config editor; a title of `@everyone` posts with no mention parsed (the request body is checked,
  not just the caption); a title changed mid-series leaves the remaining posts under the old one.
- **Console reader:** an absent store and a malformed capture each return `degraded` with a reason,
  never an empty healthy result — checked against the running build, not only `vitest`.

Fixtures come from the probe, cut to a few rows, with every header, cookie, token and account field
removed before they are committed — the repo is public.

## Not in scope

- Any other page, the ETF and Index tabs, the full lists behind the arrows, and intraday captures —
  each a separate decision.
- Reading their calendar into ours. It stays a check.
- Folding the browser, cooldown and notify helpers shared with `fetch_vendor_edition.py`. Copied
  first; folded only after both exist and the bodies are compared with identifiers normalised.

## Open questions

- Is Playwright a declared dependency anywhere, or only installed by hand for the vendor collector?
  It should be declared once, for both scripts.
- Headed (minimised) or headless by default, once the probe shows whether the site treats them
  differently.
- Whether the side markers (price dots, spread sign) are readable — if so, a later decision on
  whether this feeds the market report's section 7 as a partial, free stand-in, labelled as such.
- A weekly week-ahead post to the channel (our calendar, with any disagreement marked)? Not in the
  series until decided.
- Is the Discord server private or public? It changes how much the redistribution check matters.

## Build order

1. Phase 0 probe; findings written into this document.
2. Phase 1 capture and validation, with fixtures.
3. Phase 3 console page (it is what the series pictures).
4. Phase 4 series with `--dry-run`, then the dedicated webhook.
5. Phase 2 calendar check.
6. Phase 5 scheduling, config and docs; `CHANGELOG.md`.

Each lands as its own pull request. Nothing here is measurement-affecting — it changes no module's
loop, ledger or gate — so none of it waits for a boundary.
