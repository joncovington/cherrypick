# Orchestrator dashboard — browser tests

The Python tests cover the dashboard's data and HTML output. The dashboard's **interactive**
behaviour — drag-to-reorder and collapsible sections — is client-side JavaScript, so it's verified
here with a headless browser instead.

`dashboard.browser.test.mjs` renders the page from a fixture model (`render_fixture.py`), serves it
from a throwaway localhost server, and drives the system Chrome through
[`puppeteer-core`](https://pptr.dev/). It checks:

- **Collapsible sections** — System collapsed by default; the meic/earnings embedded dashboards
  expanded and the gex embed collapsed by default; EOD/logs expanded; toggling persists across a
  reload.
- **Drag-to-reorder** — sections and embeds reorder by their grip handle; a dragged section never
  lands past the footer; embeds reorder on drop only (no live iframe reload thrash); order persists
  across a reload; **Reset layout** restores the original order and clears storage.

It uses the **system** Chrome/Chromium (no browser download). It **skips cleanly** (exit 0) when
Chrome, Python, or `puppeteer-core` isn't available, so it never breaks a machine that can't run it.

## Run it

```bash
cd packages/orchestrator/tests/browser
npm install        # fetches puppeteer-core (no bundled Chromium)
npm test
```

Set `CHROME_PATH` if your browser isn't in a standard location:

```bash
CHROME_PATH="/path/to/chrome" npm test
```
