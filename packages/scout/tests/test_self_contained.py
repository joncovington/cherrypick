"""The shell page must be self-contained: every fetched resource resolves locally under /static/,
with exactly one allowed exception -- the Apache-2.0 attribution link Lightweight Charts requires
(see static/vendor/LICENSES.md). Modeled on flies' `test_page_is_self_contained`, but scanning
*fetched-resource* attributes (`src=`/`href=` on <script>/<link>/<img>) rather than a raw substring
scan, since that attribution anchor is a legitimate `<a href="https://...">`.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "cherrypick" / "scout" / "static"

_TAG = re.compile(r"<(script|link|img)\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'(src|href)\s*=\s*"([^"]+)"', re.IGNORECASE)

_ALLOWED_REMOTE = {"https://www.tradingview.com/"}


def _fetched_resources(html: str) -> list[str]:
    resources = []
    for tag in _TAG.finditer(html):
        m = _ATTR.search(tag.group(0))
        if m:
            resources.append(m.group(2))
    return resources


def test_index_html_exists_and_has_vendor_assets():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "/static/vendor/htmx.min.js" in html
    assert "/static/vendor/tabulator.min.js" in html
    assert "/static/vendor/lightweight-charts.standalone.production.js" in html
    assert "/static/vendor/alpine.min.js" in html


def test_every_fetched_resource_is_local_or_the_allowed_attribution(monkeypatch=None):
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for resource in _fetched_resources(html):
        if resource.startswith(("http://", "https://")):
            assert resource in _ALLOWED_REMOTE, f"fetched resource reaches out to {resource}"
        else:
            assert resource.startswith("/static/"), f"local resource not under /static/: {resource}"


def test_the_attribution_link_is_a_plain_anchor_not_a_fetched_resource():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="https://www.tradingview.com/"' in html
    # It must appear on an <a> tag (a plain link, not a <link>/<script>/<img> fetch).
    anchor_match = re.search(r'<a[^>]*href="https://www\.tradingview\.com/"[^>]*>', html)
    assert anchor_match is not None


def test_vendor_directory_has_a_license_manifest():
    manifest = STATIC_DIR / "vendor" / "LICENSES.md"
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    for name in ("lightweight-charts", "tabulator", "htmx", "alpine"):
        assert name in text.lower()
