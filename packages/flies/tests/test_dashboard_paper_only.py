"""`--paper-only` must be a guarantee, not a hidden control.

The orchestrator embeds this dashboard in an iframe under a card badged PAPER. That badge was a
promise this module could not keep: `--source` only ever affected `--json`, so the served page always
offered a "live — real money" selector and a viewer could switch the embedded card to the live ledger
while the surrounding suite dashboard still read PAPER.

Hiding the dropdown is not sufficient — `/api/data?source=live` is a plain GET on a loopback port,
reachable from the iframe's own console or a stray bookmark. So the server refuses, and these tests
pin both halves plus the failure mode that stripping the control introduced.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

from cherrypick.flies import dashboard as d


def _serve(port: int, allow_live: bool):
    t = threading.Thread(
        target=d.serve, args=(port,), kwargs={"open_browser": False, "allow_live": allow_live}, daemon=True
    )
    t.start()
    for _ in range(50):  # wait for the socket rather than sleeping a fixed guess
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("dashboard did not come up")


def _get(port: int, path: str) -> str:
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).read().decode()


def test_paper_only_strips_the_source_selector_from_the_page():
    _serve(8894, allow_live=False)
    page = _get(8894, "/")
    assert '<select id="source-select"' not in page
    assert "real money</option>" not in page


def test_paper_only_refuses_a_direct_live_request():
    """The half that holds when the request does not come from the page we served."""
    _serve(8895, allow_live=False)
    payload = json.loads(_get(8895, "/api/data?source=live"))
    assert payload["source"] == "paper", "a paper-only server must never read the live ledger"


def test_the_standalone_dashboard_keeps_both_sources():
    """Only the orchestrator's embed is locked down; running the module's own dashboard directly is
    still a legitimate way to look at the live pilot."""
    _serve(8896, allow_live=True)
    page = _get(8896, "/")
    assert '<select id="source-select"' in page
    assert json.loads(_get(8896, "/api/data?source=live"))["source"] == "live"


def test_stripping_the_control_does_not_break_the_rest_of_the_page():
    """The regression that stripping introduced: an unguarded
    `$('#source-select').onchange = ...` throws on null and halts the whole script, taking the
    arm/symbol handlers and refresh() with it — a dead dashboard rather than a paper-only one."""
    paper = d._SOURCE_SELECT_RE.sub("", d.HTML)
    assert "if (sourceSel)" in paper, "the handler must be null-guarded"
    assert 'id="arm-select"' in paper and 'id="symbol-select"' in paper


def test_the_selector_strip_survives_reformatting_of_the_template():
    """Matched by regex rather than an exact string: the markup carries an em dash and its own
    indentation, and pinning those byte-for-byte fails open the next time the template is touched."""
    assert d._SOURCE_SELECT_RE.search(d.HTML), "the selector block must still be recognisable"
    assert d.HTML.count("<label") - d._SOURCE_SELECT_RE.sub("", d.HTML).count("<label") == 1
