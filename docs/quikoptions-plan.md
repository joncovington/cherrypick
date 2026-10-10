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
Phase 6). It is not that section: it is a top-ten list per table, not the full tape. It does carry
a side: the dot beside each trade's price has a tooltip with the site's own classification, where
the fill sat in the market, and an edge figure ("Neutral / Mid Market / Edge: -0.38"). That is
recorded as the site's word, never as our own bought/sold call.

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
  a dwell and a slow scroll on the page, then the calendar reached through the menu as a person
  would. No request is sent that the page itself would not send: the site renders on its server
  and the data is read from the page as it is drawn.
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
  viewing: the terms are re-read for redistribution before the series is switched on. The posts
  carry no source attribution (decided 2026-10-03; the server is private).

## Phase 0 — probe (one visible session, a person present)

`python scripts/fetch_quikoptions.py probe` opens Chrome visibly on the profile. The person signs
in. The probe loads the Hot Options Report, waits, scrolls slowly to the footer, clicks Birdseye's
Trades / Volume / Premium in turn, then — after a 20-45 s pause — opens Resources > Calendars
through the menu and steps through each calendar tab it finds. Every response (URL, status, content
type, body) is written to `~/.cherrypick/data/quikoptions/probe/<timestamp>/`, with request headers
dropped.

### What the 2026-10-02 probe settled

Run 19:55-20:01 MDT (21:55 ET), signed in by hand, with the person also clicking around the site
during the run. 3,553 records, in `probe/20261002-195525/`.

| Question | Answer |
|---|---|
| JSON, a stream, or server-rendered? | **Blazor Server.** The page is drawn on the site's server and DOM changes arrive as binary render batches over one websocket (`wss://app.quikoptions.com/_blazor`); there is no JSON data call at all. So the capture **reads the rendered DOM**, which is also exactly what a person sees. |
| One endpoint per table? | Neither: one websocket for everything. The report is six `table.quikgrid` elements, each under its own heading: Birdseye, Top Outrights, Top Sweeps, Top Spreads, Top VolOverOI (OI > 100), Top VolOverOI (Openings). Table ids are random per render, so tables are found by heading. |
| Do lower tables load on scroll? | All six were in the first snapshot, before any scroll. The scroll is courtesy, not a requirement. |
| Trades / Volume / Premium? | Not yet observed (the probe's clicks missed while the page was elsewhere). Phase 1 captures the default, Trades; the other two are a follow-on. |
| Date and market tab? | The page shows the session (`10/2/2026`) above the tables; that is the validation date. Stock is the default tab. |
| Price dots, spread sign? | Each dot carries a tooltip: a sentiment, where the fill sat (e.g. "Mid Market"), and an edge. Spread price is negative on some rows (`-0.22` with premium `-921,800`), so the sign is the site's credit/debit convention — to be confirmed on more rows before `cr`/`db` is shown. |
| Birdseye precision? | Display only (`421.8K`); no raw count anywhere in the DOM. Checks use a rounding tolerance. |
| Which calendars? | **Economic** and **IPO**, filters ALL / COUNTRY. No earnings, dividend, holiday or expiry calendar. |
| Window, times, zone? | Economic's default view ("Upcoming") ran Fri 10/2 to Fri 10/9: 99 US events, all USD, each with an ET time, an impact (H / M / L), and previous / estimate / actual / change. |
| Sign-in | Auth0 (`quikoptions.us.auth0.com`), by hand, in the profile. How long it lasts is measured by the first scheduled runs. |
| Badges | The `+5` beside a symbol is a paid feature ("Subscribe to see the 5 badges"). Nothing captured here is behind it. |

**First comparison, by hand.** Their High-impact events against `events.day_events` for 10/2-10/9:
NFP, ISM Services, FOMC minutes and Claims agree on date and time. **One disagreement, and it is
ours:** they show Michigan Consumer Sentiment (Oct, preliminary) on Fri 10/9 at 10:00, and our
calendar has nothing that day while calling the day `known`. FRED's release 91 lists only the
*final* reading (7/31, 8/28, 9/25, 10/23), so the preliminary — the one that moves the market — has
never been on our calendar. A bug fix in `cherrypick.core.events`, landing on its own.

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
| `hot-options [--jitter MIN] [--headless] [--no-calendar]` | The daily capture: the report, then the calendar (Phase 2) |
| `validate FILE...` | Re-run the checks over saved files, offline |

A scheduled run starts after a random delay (`--jitter 25` from the job; none by hand), loads the report, waits until all six
tables are drawn, dwells 30-60 s with a slow scroll, and saves `hot-options/YYYY-MM-DD.json`: the
parsed tables, the session date the page states and the capture time, beside
`hot-options/YYYY-MM-DD.html` (the six tables' own HTML, verbatim but for icons and Blazor's
`<!--!-->` markers: about 250 KB a day) so a parser fix can be re-run over every saved day without
another visit. Never the whole page, which carries the account's name and email; a fragment that
contains an address is refused even as a reject. Each row keeps what the cells say plus what their
attributes say: the full company name, the call/put badge, and the side tooltip.

**The checks, each an identity the page already satisfies** (verified on the 2026-10-02 screenshots):

- Birdseye: the size buckets sum to Total (TSLA: 762.4K against 762.5K, to display rounding), and
  Calls + Puts = Total (462.9K + 299.6K = 762.5K). The page shows only rounded counts, so the
  tolerance is the sum of each cell's own rounding (±0.05K on a `K` cell, 0 on a whole number).
- Spreads and sweeps: Premium = |price| × size × 100 (AI: 0.47 × 41,900 × 100 = 1,969,300).
  The one exception is a **mixed** spread, a structure with a call and a put leg (the site's `M`:
  a risk reversal, a strangle), first seen 2026-10-07. Its premium does not satisfy the identity
  (RUN 6/12 RR at 0.16 × 113,000 printed 1,130; VALE and BABA off by other factors, no formula fits
  all three), so it is kept as the site's figure with `premium_unverified`. It is still required to
  read. It never sizes, scores or ranks a derived flow, is never the largest trade, and is marked
  "unverified" in the console and the Discord post. `reparse` promotes a rejected day whose own
  HTML passes after a parser fix.
- Vol/OI: V/OI = Volume ÷ OI (SPCX: 135,071 ÷ 119 = 1,135.05); the Openings table has OI = 0,
  or since 2026-10-09 a little more (TAL: 20,062 ÷ 1), so an opening's OI is at most 100 — past
  that it belongs to the OI > 100 panel — and its V/OI divides the same way.
- The session date the page states is the session being saved, and every table is present and
  non-empty.

**Browser mode.** Headless with the matching Chrome user-agent (the vendor collector's mode) by
default since 2026-10-08: a window opening on the desktop is disruptive, and nothing showed the site
treating headless differently. `headed: true` runs it visibly, for a session someone is watching for
errors; a hand-run `hot-options` is visible unless given `--headless`.

## Phase 2 — the calendar check

**Capture.** Every run, in the same session: after a 20-45 s pause, Resources > Calendars through
the menu, the Economic tab's default Upcoming view. Its window is only about a week (today to the
next Friday), so a weekly fetch would leave gaps; daily also records each release's actual on its
day. The IPO tab is a follow-on. Saved as `calendar/YYYY-MM-DD.json` (and `.html`), one per fetch,
never overwritten, so their own revisions show up as differences between fetches.

**Comparison** — a pure function in `cherrypick.core.events` over the saved file and our stores,
no network:

- An alias table maps their release names to our labels (CPI, NFP, PCE, GDP, FOMC, Retail, ISM,
  ...). An unmapped name is reported as unmapped, never dropped: a dropped row reads as agreement.
- For each date both sides cover: **match**, **date mismatch**, **time mismatch**, **only theirs**,
  **only ours**, and days where our side is `unknown` (a gap in our sources, not a disagreement).
- Against `cherrypick.core.events.day_events` only: the site has no earnings, dividend, holiday or
  expiry calendar. Their High-impact events must each be matched or reported; Medium and Low are
  listed when they map to one of our labels and otherwise ignored, so speeches and CFTC positions do
  not drown the report.

`python scripts/fetch_quikoptions.py check-calendar` prints the result; the daily capture notifies
only when something disagrees. Their calendar can be the wrong one: each disagreement is settled at
the agency's own page, and a correction on our side goes through `backfill-events --restamp`.

## Phase 3 — the console page

**Built 2026-10-03** as "Options flow" at `/flow` (the Morning report already has an OCC card called
"Hot options"): tabs `today`, `birdseye`, `trades`, `spreads`, `vol / OI`. The calendar card waits
for Phase 2's comparison. What the page shows beyond the site's cells is derived by the capture
(`derive`: outright premium, spread direction, trades printed together, the four trade-size bands,
names across tables, premium and trade counts by the site's side, the largest trade), because the
console computes nothing; `fetch_quikoptions.py reparse` rebuilds saved days after a rule changes.
The `today` cards a Discord post can capture: `Birdseye`, `Names across tables`, `Largest
outrights`, `Top sweeps`, `Top spreads`, `Vol / OI`, each titled `<name> — <session>` and each
drawing an SVG, which `ui-check --card` requires.

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

**Built 2026-10-03** (`scripts/quikoptions_post.py`), shorter than first drawn: the day does not
need every table to be told.

**The webhook is a choice** (`quikoptions.post_webhook`): `notify`, the suite's existing Discord
notify webhook, or `reporting`, the reporting channel's keyring entry `discord_reporting` under the
notification stack's service (`cherrypick secrets-set --channel discord_reporting`, or the settings
surface); `dedicated`, its first name, still resolves. A dedicated webhook is never a push channel: listed in `notify.channels` the notifier skips
it and `doctor` warns, so no suite alert can land there. **Never a fallback**: with the chosen
webhook not stored, the run posts nothing and says why. `--webhook notify|reporting` overrides the
choice for one run (the first test, 2026-10-03, posts to the notify channel).

**The series**: a header embed — the title, the date, four fields (most traded, the largest trade,
bullish / bearish premium with trade counts, the names in two or more tables) and a footer with the
capture time (no source attribution, decided 2026-10-03) — then the cards in `quikoptions.post_cards`, two to a message so Discord
shows them side by side. Default: `["Largest by contracts", "Top sweeps"]`, one message. `Largest by contracts` is the site's
"Top Outrights": single-leg trades ranked by number of contracts, as the site ranks them (2026-10-03). Any of the six
`today` cards can be listed, plus `Events` (the session's high-impact releases with actual against
estimate, and the next ones, from the calendar capture); an unknown name refuses the run.

**As of 2026-10-03, after the test posts:** the default is **`singles`** — one screen capture a
message, so each reads full width on a phone, with **no header message**: the series' title line
rides on the first picture, and each picture goes out under its own bold title — with **Derived flow, Trades (the site's
Birdseye, renamed; the old name still resolves) and Events**. The Derived flow capture comes from a
hidden **post page** (`/post/flow?session=…`: outside the shell, in no nav, 1,000 px wide, without the
four factors or the open-interest column), and so do **Top spreads** (the day's largest spreads by
contracts, in the derived flow's words) and **Events**, a table of the session's high-impact releases
(actual, estimate, previous) and the next week's; Trades from the Options flow page. Each picture
goes out under a bold text title. **Flags are abbreviated** in a narrow column — a name of four
characters or fewer as itself, a longer one as a code (SWP sweep, OPEN opening, V>OI volume over OI,
≤7d, DITM deep ITM, LOT lottery, NMAX near max, roll, LNK linked, PAIR paired prints, OPP paired
opposite, EARN earnings event, XDIV before ex-dividend, ANTI sentiment opposite, NEUT sentiment
neutral, ΔCHK delta check; symbols were tried and dropped the same day) — keyed under each table
with only the flags it shows; one home for the codes and the meanings (`FLAG_ABBR`, `FLAG_KEY`),
shared by the console and the post page.
Default sections: Derived flow, Trades, Top spreads, Events. The post page is 1,280 px for the two
flow tables (so a row's flags fit on one line) and 1,000 px for the rest. Every flow reads
in one order: symbol, date, strike, kind (`SMCI 09 Oct 26 43.5/45.5C call spread`). The header
leads with the derived flow (bullish and bearish names, the top flow, the largest unread trade, and
yesterday's calls a day on). Two more posts, both narrow text: the **morning follow-up** after the
open-interest check (opened, closed, mixed), and the **Friday scorecard** (the week's checks and
confirmations, descriptive only until the fixed test). The morning post waits until the overnight
open interest is actually out: on a Saturday every contract still showed Friday's starting figure.

**Four styles** (`quikoptions.post_style`), tried side by side on the notify channel on 2026-10-03:
`cards` (the header, then card images two to a message), `singles` (one image a message), `embed`
(one embed of small monospace tables, no images) and `text` (plain text tables, split at a section
past Discord's 2,000 characters). In the image styles Events is a header field. **Default: `text`** (chosen 2026-10-03); the others stay
configurable. `--style`,
`--cards`, `--title` and `--webhook` override the config for one run, checked by the same rules.

Pictures come from the console page via `tools/ui-check.mjs --card`, which refuses rather than
crops the wrong card, and the card titles carry the session date, so a page still showing another
day fails the run instead of posting it under today's caption (the payoff post's rule). Captions are
built from the saved capture, never from the picture.

**The title is configurable** (`quikoptions.post_title`, default `Hot options`). It replaces only the
leading name: the session date is always appended (`TCP Options Report — Fri 2 Oct 2026`; the
`(stocks)` suffix was dropped 2026-10-03), so no title can drop the date. It is trimmed, must be non-empty,
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

Built 2026-10-03. The report lands 30-60 minutes after the close; every time is ET and configurable.

| Job | Default | Runs | Needs |
|---|---|---|---|
| `quikoptions-capture` | `at` 16:30, then 0-`jitter_minutes` (10) of random delay | `fetch_quikoptions.py hot-options --jitter N` (`--headless` when `headed` is false) | `enabled` |
| `quikoptions-score` | `score_at` 16:50 | `quikoptions_flow.py score --require-today --wait 45`, which also refreshes `review.json` | `enabled` |
| `quikoptions-post` | `post_at` 17:00 | `quikoptions_post.py --wait 45` | `post` |
| `quikoptions-confirm` | `confirm_at` 08:30, the next trading morning | `quikoptions_flow.py confirm` | `enabled` |
| `quikoptions-morning` | `morning_at` 08:45 | `quikoptions_post.py --kind morning --wait 45` | `post`, `post_morning` |
| `quikoptions-weekly` | `weekly_at` 17:15, every trading day | `quikoptions_post.py --kind weekly`, which posts only on the week's last trading day (the Thursday before a Good Friday), and only to the notify webhook | `post`, `post_weekly` |

All trading days only, each with its `CATCHUP_MINUTES` entry (four to five hours: a late capture is
the same page, and the morning pair is still worth posting before the next session's capture).
The calendar is read in the capture's own session, so it needs no job of its own.

**Each step waits for the one before it.** The scheduler fires a daily job once whatever its outcome,
so a machine waking at 19:00 fires the capture, the score and the post in the same tick. `--wait`
(45 minutes, past the capture's jitter and its paced run) has the score wait for today's capture and
the post for today's scored flows; the morning post waits for the confirmation. Past the bound the
score finds nothing and stops, the daily post goes out without its derived flow, and the morning
post skips. `--require-today` keeps a day with no capture from re-scoring an earlier day under a
post already made.
- `config.example.json`: the block with a `_comment` (what it fetches, the pacing, the cooldown, how
  to sign in, how to store the webhook, the terms note), and a row in
  `docs/configuration-and-storage.md`.
- The console's Config page: `quikoptions.post` and `quikoptions.post_title` join the allow-list, so
  the series can be paused or retitled from the console without touching the capture. The title
  is validated by the orchestrator's config editor with the same rule the script applies, so the
  console cannot save one the script would refuse. `enabled` stays out of it: turning the capture on
  needs a sign-in, which the console cannot do.

## Phase 6 — derived flow (built 2026-10-03)

The site's side is a lean, not a fact: where the fill sat says who was in a hurry, not whether the
trade opened or closed, hedged or bet. `scripts/quikoptions_flow.py` turns each day's lists into
**derived flows** — one row per order — and scores them on what the suite can check.

**A row** is an outright, a sweep or a spread; prints sharing a symbol and a millisecond are one order
(paired prints), and spreads printed together are a roll when their views differ. Each carries its
read (bought/sold from the edge at ±0.5, or a spread's sign; otherwise *unread*), its view, its delta
(from the volatility the trade's own price implies at the broker's close; ±1 at or under intrinsic;
the name's 30-day IV as a fallback), **Δ$** (contracts × 100 × |delta| × close: the stock-equivalent
size), and flags.

**The score**, 0-100 and signed by the view, is size × conviction × purity × opening — each factor and
constant named in the script and in its docstring. Decided 2026-10-03:

| Question | Decision |
|---|---|
| Rank by | **Δ$**, premium shown beside it (premium overstates in-the-money trades) |
| Size scale | **fixed** $100K-$50M log scale, so scores compare across days |
| Read cutoff | edge ±0.5; the site's own fill label is a second vote: disagreeing halves conviction |
| Unread flows | listed with their Δ$, **never ranked** |
| Sold options | purity ×0.75: a sold call or put is often income or an overwrite |
| Earnings | flagged only when the contract expires within 30 days after the next report (an event bet) |
| Where | a `derived flow` tab and two `today` cards on the console; the first section of the text post (top 5 and the net by name) |

**Data from the broker** (read-only market data, the shared login), at scoring time: each name's
official close, each contract's starting open interest and day volume, each name's 30-day IV and next
earnings date. Ex-dividend dates from the technicals store (the broker's looked stale). ADRs the
broker does not list have no close, so no Δ$; they rank on premium, halved.

**Three moments.** `score` after the capture (16:50 ET) writes `<session>.flow.json`. `confirm` the
next morning reads each contract's open interest again: each flow becomes `opened`, `closed` or
`mixed`, and a confirmed score sits beside the first (the console and the post show the confirmed one
once it exists). Every `score` run also records the **outcome** for the sessions 1 and 5 trading days
back — each name's close against its close then — the record the constants are to be judged on.

**Verification (built 2026-10-03)**, four layers, each on the `derived flow` tab's Checks card:

1. *The day's own data.* The read against the site's own sentiment (2026-10-02: 16 agree, 0
   opposite). The **broker's delta is primary** — the streamer's greeks at the close for the day's
   contracts, requested through a leg-source database the producer re-queries, so no restart — and
   the model's is checked against it: on 2026-10-02 the model was within 0.10 on 18 of 20, and the
   two it missed (a deep in-the-money 2027 VST put, TSM) are exactly where a trade price taken
   hours before the close goes wrong. The next morning, each close against Dolt's.
2. *Opening against closing* from the next morning's open interest (the confirmation).
3. *A hand audit:* the top flows checked against the broker's Time & Sales, recorded with
   `quikoptions_flow.py audit --rank N --tape bought|sold|middle`, reported as an agreement rate.
4. *The fixed test*, written here on 2026-10-03 before any outcome existed, and not to be changed
   after looking: **after 40 sessions with a 5-session outcome, flows scoring 30 or more (either
   sign) must move their stock their way more often than all read flows do, and more often than both
   simpler reads of the same days — the site's own sentiment weighted by premium, and our net delta
   dollars alone.** `quikoptions_flow.py review` runs it; until 40 sessions it reports progress only.
   A flat move counts as neither a hit nor a miss.

Also decided: the second vote is the site's **sentiment**, not its fill wording ("Mid Market" is
broader than its edge; where the wording looked like a disagreement the sentiment agreed). Opposite
sentiment halves conviction; neutral takes a quarter off.

**Not yet:** a paid tape (one month of ThetaData, every print against
the quote) to measure the read at scale is deferred until a few weeks of confirmations exist.

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
- **Webhook:** `notify.channels` containing `discord_reporting` is refused; with only the general
  Discord webhook stored, the series posts nothing and names the missing entry.
- **Series:** a failure after post 2 leaves the marker at 2, and the re-run sends 3-5 only; a capture
  whose session differs from the card titles posts nothing; a `.rejected` capture posts nothing.
- **Title:** an empty, multi-line or 81-character title is refused by both the script and the
  config editor; a title of `@everyone` posts with no mention parsed (the request body is checked,
  not just the caption); a title changed mid-series leaves the remaining posts under the old one.
- **Console reader:** an absent store and a malformed capture each return `degraded` with a reason,
  never an empty healthy result — checked against the running build, not only `vitest`.

The tests' tables are built to the markup the probe saw, never the site's own HTML (their data, and
the repo is public) — the vendor collector's rule. Separate tests run every check over the real
saved captures when they are on the machine, and skip elsewhere.

## Not in scope

- Any other page, the ETF and Index tabs, the full lists behind the arrows, and intraday captures —
  each a separate decision.
- Reading their calendar into ours. It stays a check.
- Folding the browser, cooldown and notify helpers shared with `fetch_vendor_edition.py`. Copied
  first; folded only after both exist and the bodies are compared with identifiers normalised.

## Open questions

- Is Playwright a declared dependency anywhere, or only installed by hand for the vendor collector?
  It should be declared once, for both scripts.
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
